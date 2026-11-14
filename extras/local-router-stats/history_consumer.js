// Shared source for generated native QML and offline Node fixtures.
// This is a bounded public projection validator, not a request-history oracle.
var fields = ['input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens',
              'reasoning_tokens', 'api_call_count', 'prompt_tokens', 'total_tokens'];
var grains = ['day', 'week', 'month', 'year'];
var statuses = ['baseline', 'delta', 'measured_zero', 'reset', 'missing', 'reappeared', 'gap'];
var providers = ['openai','openai-codex','anthropic','openrouter','google','gemini','groq',
                 'deepseek','nous','xai','mistral','cerebras','together','fireworks'];
// Mirrors usage_history: 512 retained identities x 61 dense buckets, 16 MiB.
var historyCellLimit = 31232, historyByteLimit = 16777216;
function requireHistory(ok) { if (!ok) throw new Error('invalid public history'); }
function shape(value, names) {
    requireHistory(value !== null && typeof value === 'object' && !Array.isArray(value));
    requireHistory(Object.keys(value).sort().join('|') === names.slice().sort().join('|'));
}
function safe(n) {
    requireHistory(typeof n === 'number' && isFinite(n) && Math.floor(n) === n && n >= 0 && n <= 9007199254740991);
    return n;
}
function plus(a,b) { return safe(a+b); }
function zeroCounts() { var o={}; for (var i=0;i<fields.length;i++) o[fields[i]]=0; return o; }
function counts(c) {
    shape(c,fields);
    for (var i=0;i<fields.length;i++) safe(c[fields[i]]);
    requireHistory(c.reasoning_tokens <= c.output_tokens);
    requireHistory(c.prompt_tokens === plus(plus(c.input_tokens,c.cache_read_tokens),c.cache_write_tokens));
    requireHistory(c.total_tokens === plus(c.prompt_tokens,c.output_tokens));
}
function addCounts(a,b) { for (var i=0;i<fields.length;i++) a[fields[i]]=plus(a[fields[i]],b[fields[i]]); }
function equalCounts(a,b) { for (var i=0;i<fields.length;i++) requireHistory(a[fields[i]]===b[fields[i]]); }
function instant(s) {
    requireHistory(typeof s==='string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(s));
    var n=Date.parse(s);
    requireHistory(isFinite(n) && new Date(n).toISOString().replace('.000Z','Z')===s && Number(s.slice(0,4))>=1970 && Number(s.slice(0,4))<=9998);
    return n;
}
function calendar(key,grain) {
    var pattern=grain==='year'? /^\d{4}$/ : grain==='month'? /^\d{4}-\d{2}$/ : /^\d{4}-\d{2}-\d{2}$/;
    requireHistory(typeof key==='string' && pattern.test(key));
    var full=key+(grain==='year'? '-01-01' : grain==='month'? '-01' : '');
    var n=Date.parse(full+'T00:00:00Z');
    requireHistory(isFinite(n) && new Date(n).toISOString().slice(0,10)===full && Number(key.slice(0,4))>=1969 && Number(key.slice(0,4))<=9998);
    if (grain==='week') requireHistory(new Date(n).getUTCDay()===1);
    return n;
}
function publicCoverage(c,hasSources) {
    shape(c,['events','observed','unallocated','pruned','prunedEvents','status','missingHistory','interpretation']);
    shape(c.events,statuses); for (var i=0;i<statuses.length;i++) safe(c.events[statuses[i]]);
    counts(c.observed); safe(c.prunedEvents);
    for (var j=0;j<2;j++) {
        var map=c[j===0?'unallocated':'pruned']; shape(map,grains);
        for (var g=0;g<grains.length;g++) counts(map[grains[g]]);
    }
    var measured=plus(plus(c.events.delta,c.events.measured_zero),c.events.gap);
    requireHistory(c.status===(measured>0?'partial-observed':hasSources?'baseline-only':'unknown'));
    requireHistory(c.missingHistory===true && c.interpretation==='sampled-counter-deltas-not-requests');
}
// Scan JSON structural tokens before parsing: bound depth and reject duplicate
// object keys (including escaped aliases), which JSON.parse would silently lose.
function uniqueJson(text) {
    // Scan in place. Qt's RegExp.exec token stream can retain enough transient
    // match data to exceed the native reader's memory budget at dense capacity.
    // JSON.parse below still owns grammar validation; this pass owns depth and
    // duplicate keys, including keys spelled with different JSON escapes.
    var stack=[], i=0;
    while (i<text.length) {
        var c=text.charCodeAt(i), top=stack.length?stack[stack.length-1]:null;
        if (c===123 || c===91) {
            stack.push({object:c===123,keys:c===123?Object.create(null):null,key:c===123});
            requireHistory(stack.length<=32); i++;
        } else if (c===125 || c===93) {
            stack.pop(); i++;
        } else if (c===34) {
            var start=i++;
            while (i<text.length) {
                var q=text.charCodeAt(i++);
                if (q===92) i++;
                else if (q===34) break;
            }
            if (top && top.object && top.key) {
                var k=JSON.parse(text.slice(start,i));
                requireHistory(!Object.prototype.hasOwnProperty.call(top.keys,k));
                top.keys[k]=true; top.key=false;
            }
        } else if (c===44) {
            if (top) top.key=top.object;
            i++;
        } else if (c===58 || c===32 || c===9 || c===10 || c===13) {
            i++;
        } else {
            requireHistory(!top || !top.object || !top.key);
            i++;
        }
    }
}
function validateHistory(data) {
    shape(data,['contract','schemaVersion','readOnly','timezone','startedAt','observedAt','generation','sources','buckets','events','coverage']);
    requireHistory(data.contract==='hermes-observed-history/1' && data.schemaVersion===1 && data.readOnly===true);
    requireHistory(typeof data.timezone==='string' && data.timezone.length<=128 && /^[A-Za-z0-9_+-]+(?:\/[A-Za-z0-9_+-]+)*$/.test(data.timezone)
                   && ['localtime','posixrules'].indexOf(data.timezone)<0 && !/^(posix|right)\//.test(data.timezone));
    var start=instant(data.startedAt), end=instant(data.observedAt); requireHistory(start<=end); safe(data.generation);
    requireHistory(Array.isArray(data.sources) && data.sources.length<=512);
    var sources=Object.create(null), retained=Object.create(null), summed=zeroCounts(), coverageEvents={}, profile=null;
    for (var st=0;st<statuses.length;st++) coverageEvents[statuses[st]]=0;
    var sumUnallocated={},sumPruned={},prunedEvents=0;
    for (var gi=0;gi<grains.length;gi++) {sumUnallocated[grains[gi]]=zeroCounts();sumPruned[grains[gi]]=zeroCounts();}
    for (var i=0;i<data.sources.length;i++) {
        var s=data.sources[i];
        shape(s,['id','provider','profile','attribution','counts','present','firstObservedAt','lastObservedAt','baselineAt','coverage']);
        requireHistory(typeof s.id==='string' && /^[a-f0-9]{64}$/.test(s.id) && !sources[s.id]);
        requireHistory(providers.indexOf(s.provider)>=0 && typeof s.profile==='string' && /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(s.profile));
        if (profile===null) profile=s.profile; requireHistory(s.profile===profile && s.attribution==='unattributed' && typeof s.present==='boolean');
        counts(s.counts); var first=instant(s.firstObservedAt), last=instant(s.lastObservedAt), baseline=instant(s.baselineAt);
        requireHistory(start<=first && first<=baseline && baseline<=last && last<=end);
        publicCoverage(s.coverage,true); sources[s.id]=s; retained[s.id]={};
        for (var g=0;g<grains.length;g++) {
            retained[s.id][grains[g]]=zeroCounts();
            addCounts(sumUnallocated[grains[g]],s.coverage.unallocated[grains[g]]);
            addCounts(sumPruned[grains[g]],s.coverage.pruned[grains[g]]);
        }
        addCounts(summed,s.coverage.observed); prunedEvents=plus(prunedEvents,s.coverage.prunedEvents);
        for (var k=0;k<statuses.length;k++) coverageEvents[statuses[k]]=plus(coverageEvents[statuses[k]],s.coverage.events[statuses[k]]);
    }
    publicCoverage(data.coverage,data.sources.length>0); equalCounts(summed,data.coverage.observed);
    requireHistory(prunedEvents===data.coverage.prunedEvents);
    for (var k=0;k<statuses.length;k++) requireHistory(coverageEvents[statuses[k]]===data.coverage.events[statuses[k]]);
    shape(data.buckets,grains); var limits=[31,13,12,5],cells=0;
    for (var g=0;g<grains.length;g++) {
        var grain=grains[g], buckets=data.buckets[grain],previous='';
        equalCounts(sumUnallocated[grain],data.coverage.unallocated[grain]);equalCounts(sumPruned[grain],data.coverage.pruned[grain]);
        requireHistory(Array.isArray(buckets) && buckets.length<=limits[g]);
        for (var b=0;b<buckets.length;b++) {
            var bucket=buckets[b];shape(bucket,['key','sources']);calendar(bucket.key,grain);
            requireHistory(bucket.key>previous);previous=bucket.key;
            requireHistory(Array.isArray(bucket.sources) && bucket.sources.length>0 && bucket.sources.length<=512);
            var seen=Object.create(null),total=0;
            for (var c=0;c<bucket.sources.length;c++) {
                var cell=bucket.sources[c];shape(cell,['id','counts','coveredSeconds','intervals']);
                requireHistory(typeof cell.id==='string' && sources[cell.id] && !seen[cell.id]);seen[cell.id]=true;
                counts(cell.counts);safe(cell.coveredSeconds);safe(cell.intervals);
                // Conservative maximum allows DST/date-line calendar spans.
                var maximum=[172800,691200,2851200,31708800][g];
                requireHistory(cell.intervals>0 && cell.intervals<=cell.coveredSeconds && cell.coveredSeconds<=maximum);
                addCounts(retained[cell.id][grain],cell.counts);total=plus(total,cell.counts.total_tokens);cells++;
            }
        }
    }
    requireHistory(cells<=historyCellLimit);
    for (var id in sources) for (var g=0;g<grains.length;g++) {
        var grain=grains[g],v=retained[id][grain];addCounts(v,sources[id].coverage.unallocated[grain]);addCounts(v,sources[id].coverage.pruned[grain]);equalCounts(v,sources[id].coverage.observed);
    }
    requireHistory(Array.isArray(data.events) && data.events.length<=512);
    var lastEvent=Object.create(null),eventEnd=start,eventCoverage=Object.create(null);
    for (var identity in sources) {
        var ec={events:{},observed:zeroCounts(),unallocated:{}};
        for (var si=0;si<statuses.length;si++) ec.events[statuses[si]]=0;
        for (var gg=0;gg<grains.length;gg++) ec.unallocated[grains[gg]]=zeroCounts();
        eventCoverage[identity]=ec;
    }
    for (var e=0;e<data.events.length;e++) {
        var event=data.events[e];shape(event,['sourceId','status','start','end','counts','allocated']);
        requireHistory(typeof event.sourceId==='string' && sources[event.sourceId] && statuses.indexOf(event.status)>=0);
        var a=instant(event.start),z=instant(event.end);requireHistory(start<=a && a<=z && z<=end && z>=eventEnd && a>=(lastEvent[event.sourceId] || start));lastEvent[event.sourceId]=z;eventEnd=z;
        requireHistory(Array.isArray(event.allocated) && event.allocated.length<=4);
        var used={};for (var ag=0;ag<event.allocated.length;ag++) {var name=event.allocated[ag];requireHistory(grains.indexOf(name)>=0 && !used[name]);used[name]=true;}
        if (['delta','measured_zero','gap'].indexOf(event.status)>=0) {
            counts(event.counts); requireHistory(z>a && (event.status==='gap')===((z-a)>21600000));
            if (event.status==='gap') requireHistory(event.allocated.length===0);
            else requireHistory((event.status==='measured_zero')===(event.counts.total_tokens===0));
        } else requireHistory(event.counts===null && event.allocated.length===0);
        var ec=eventCoverage[event.sourceId];ec.events[event.status]=plus(ec.events[event.status],1);
        if (event.counts!==null) {
            addCounts(ec.observed,event.counts);
            for (var gg=0;gg<grains.length;gg++) if (event.allocated.indexOf(grains[gg])<0) addCounts(ec.unallocated[grains[gg]],event.counts);
        }
    }
    for (var identity in sources) {
        var ec=eventCoverage[identity],coverage=sources[identity].coverage,retainedEvents=0,lifetimeEvents=0;
        for (var si=0;si<statuses.length;si++) {
            var status=statuses[si];requireHistory(ec.events[status]<=coverage.events[status]);
            retainedEvents=plus(retainedEvents,ec.events[status]);lifetimeEvents=plus(lifetimeEvents,coverage.events[status]);
        }
        requireHistory(lifetimeEvents-retainedEvents===coverage.prunedEvents);
        var pairs=[[ec.observed,coverage.observed]];
        for (var gg=0;gg<grains.length;gg++) pairs.push([ec.unallocated[grains[gg]],coverage.unallocated[grains[gg]]]);
        for (var pp=0;pp<pairs.length;pp++) for (var ff=0;ff<fields.length;ff++) {
            var name=fields[ff];requireHistory(pairs[pp][0][name]<=pairs[pp][1][name]);
            if (coverage.prunedEvents===0) requireHistory(pairs[pp][0][name]===pairs[pp][1][name]);
        }
    }
}
function parseHistory(content) {
    var bad={state:'malformed',summaries:[],bars:[],maximum:0,help:'Unsupported or malformed private history; no totals inferred.'};
    try {
        requireHistory(typeof content==='string' && content.length>0 && content.length<=historyByteLimit);
        requireHistory(unescape(encodeURIComponent(content)).length<=historyByteLimit);
        uniqueJson(content);var data=JSON.parse(content);validateHistory(data);
        var summaries=[],bars=[],maximum=0,coverageHelp=[];
        for (var g=0;g<grains.length;g++) {
            var buckets=data.buckets[grains[g]],latest=buckets[buckets.length-1],value=null;
            if (latest) {value=0;for (var i=0;i<latest.sources.length;i++) value=plus(value,latest.sources[i].counts.total_tokens);}
            summaries.push({grain:grains[g],key:latest?latest.key:'',value:value});
            coverageHelp.push(grains[g]+': '+buckets.length+' retained; latest sources '+(latest?latest.sources.length:0)+'/'+data.sources.length+'; unallocated '+data.coverage.unallocated[grains[g]].total_tokens+'; pruned '+data.coverage.pruned[grains[g]].total_tokens+'.');
        }
        // At most seven explicitly dated display slots. Missing slots are unknown.
        var days=data.buckets.day;
        if (days.length) {
            var latestDay=calendar(days[days.length-1].key,'day'),byDate={};
            for (var d=0;d<days.length;d++) {var sum=0;for (var s=0;s<days[d].sources.length;s++) sum=plus(sum,days[d].sources[s].counts.total_tokens);byDate[days[d].key]=sum;}
            for (var j=6;j>=0;j--) {
                var key=new Date(latestDay-j*86400000).toISOString().slice(0,10);
                var n=Object.prototype.hasOwnProperty.call(byDate,key)?byDate[key]:null;
                bars.push({key:key,value:n});if (n!==null) maximum=Math.max(maximum,n);
            }
        }
        var measured=plus(plus(data.coverage.events.delta,data.coverage.events.measured_zero),data.coverage.events.gap);
        var state=data.sources.length===0?'empty':measured===0?'baseline':data.coverage.observed.total_tokens===0?'zero':'observed';
        return {state:state,summaries:summaries,bars:bars,maximum:maximum,missingHistory:true,
                data:data,
                help:'Sampled canonical cloud deltas, not requests or all-account usage. Latest retained periods in '+data.timezone+'. Recording '+data.startedAt+' to '+data.observedAt+'. Baselines are undated; missing dates/sources are unknown; 0 is measured. + marks a known selected-source lower bound. Partial coverage; pruned events '+data.coverage.prunedEvents+'. '+coverageHelp.join(' ')+' Local overlap excluded; reasoning is already output. Not added again to pinned totals.'};
    } catch (error) { return bad; }
}

// Query only the validated public projection retained by parseHistory. User
// controls never re-read files, infer dates, or add this view to pinned totals.
// Work is bounded by 512 identities/31,232 retained cells; no O(1) claim.
function selectHistory(report, options) {
    var out = {state:report ? report.state : 'unavailable', summaries:[], bars:[],
               sources:[], selectedSources:[], maximum:0, selectionEmpty:false,
               selectionError:false, grain:'day', metric:'total_tokens', mode:'combined'};
    if (!report || !report.data) return out;
    try {
        if (options === undefined || options === null) options = {};
        requireHistory(typeof options === 'object' && !Array.isArray(options));
        var optionKeys = Object.keys(options);
        for (var k = 0; k < optionKeys.length; k++)
            requireHistory(['sources','grain','metric','mode'].indexOf(optionKeys[k]) >= 0);
        var grain = options.grain === undefined ? 'day' : options.grain;
        var metric = options.metric === undefined ? 'total_tokens' : options.metric;
        var mode = options.mode === undefined ? 'combined' : options.mode;
        requireHistory(grains.indexOf(grain) >= 0 && fields.indexOf(metric) >= 0
                       && ['combined', 'multiple'].indexOf(mode) >= 0);
        out.grain = grain; out.metric = metric; out.mode = mode;
        var data = report.data, wanted = Object.create(null), all = options.sources == null;
        if (!all) {
            // Panel controls retain plain JS arrays (including explicit []).
            requireHistory(Array.isArray(options.sources) && options.sources.length <= 512);
            for (var i = 0; i < options.sources.length; i++) {
                requireHistory(typeof options.sources[i] === 'string'
                               && /^[a-f0-9]{64}$/.test(options.sources[i]) && !wanted[options.sources[i]]);
                wanted[options.sources[i]] = true;
            }
        }
        var selectedMeasured = 0, selectedTotal = 0;
        for (var i = 0; i < data.sources.length; i++) {
            var source = data.sources[i];
            out.sources.push({id:source.id, provider:source.provider, present:source.present,
                              label:source.provider + ' · ' + source.profile + ' · ' + source.id.slice(0,8)});
            if (all || wanted[source.id]) {
                out.selectedSources.push(source.id);
                var events = source.coverage.events;
                selectedMeasured = plus(selectedMeasured,plus(plus(events.delta,events.measured_zero),events.gap));
                selectedTotal = plus(selectedTotal,source.coverage.observed.total_tokens);
            }
        }
        out.selectionEmpty = out.selectedSources.length === 0;
        out.state = out.selectionEmpty ? 'empty' : selectedMeasured === 0 ? 'baseline'
                    : selectedTotal === 0 ? 'zero' : 'observed';
        function bucketValue(bucket) {
            var rows = Object.create(null), lower = Object.create(null), value = 0, known = 0, lowerBound = false, series = [];
            if (bucket) for (var i = 0; i < bucket.sources.length; i++) {
                var cell = bucket.sources[i];
                // A null counter is one this source cannot measure: unknown, not zero.
                if (cell.counts[metric] === null) continue;
                rows[cell.id] = cell.counts[metric];
                lower[cell.id] = cell.partial === true;
            }
            for (var i = 0; i < out.selectedSources.length; i++) {
                var id = out.selectedSources[i], present = Object.prototype.hasOwnProperty.call(rows,id);
                if (present) { value = plus(value,rows[id]); known++; if (lower[id]) lowerBound = true; }
                if (mode === 'multiple') series.push({id:id,
                    value:present ? rows[id] : null, colorIndex:parseInt(id.slice(0,2),16) % 6});
            }
            return {value:known ? value : null, known:known,
                    partial:known > 0 && (known < out.selectedSources.length || lowerBound), series:series};
        }
        for (var g = 0; g < grains.length; g++) {
            var buckets = data.buckets[grains[g]], latest = buckets[buckets.length-1];
            var value = bucketValue(latest);
            out.summaries.push({grain:grains[g], key:latest ? latest.key : '',
                                value:value.value, known:value.known, partial:value.partial});
        }
        if (out.selectionEmpty) return out;
        var buckets = data.buckets[grain];
        if (!buckets.length) return out;
        var byKey = Object.create(null), latestKey = buckets[buckets.length-1].key;
        for (var b = 0; b < buckets.length; b++) byKey[buckets[b].key] = buckets[b];
        var latestDate = calendar(latestKey,grain), slots = grain === 'year' ? 5 : 7;
        for (var j = slots-1; j >= 0; j--) {
            var date = new Date(latestDate);
            if (grain === 'day' || grain === 'week') date.setUTCDate(date.getUTCDate()-j*(grain === 'week' ? 7 : 1));
            else if (grain === 'month') date.setUTCMonth(date.getUTCMonth()-j);
            else date.setUTCFullYear(date.getUTCFullYear()-j);
            if (date.getUTCFullYear() < 1969) continue;
            var key = date.toISOString().slice(0,grain === 'year' ? 4 : grain === 'month' ? 7 : 10);
            var value = bucketValue(byKey[key]); value.key = key;
            out.bars.push(value);
            if (value.value !== null) out.maximum = Math.max(out.maximum,value.value);
        }
        return out;
    } catch (error) {
        out.selectionError = true; out.bars = []; out.summaries = []; out.maximum = 0;
        return out;
    }
}

// Unified dated roll-up: Cloud deltas + Local router samples per calendar day.
// parseRollup returns the report shape selectHistory already reads, so the
// calendar's metrics, grains and combined view work unchanged.
var rollupDays = 366, rollupClockSlack = 300000, dayMs = 86400000;
function isoDay(n) { return new Date(n).toISOString().slice(0,10); }
function mondayOf(key) {
    var n = calendar(key,'day');
    return isoDay(n - ((new Date(n).getUTCDay() + 6) % 7) * dayMs);
}
// Calendar days in a period up to today, so a month with unrecorded days is a lower bound.
function elapsedPeriodDays(key, grain, today) {
    var start = calendar(key,grain), date = new Date(start), end = start;
    if (grain === 'week') end = start + 6 * dayMs;
    if (grain === 'month') end = Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0);
    if (grain === 'year') end = Date.UTC(date.getUTCFullYear(), 11, 31);
    return Math.round((Math.min(end, calendar(today,'day')) - start) / dayMs) + 1;
}
// The typed Claude CLI can establish only total_tokens. Its other counters
// must all remain null; ordinary canonical cloud counts stay strictly checked.
function rollupCloudCell(c, provider) {
    if (provider === 'anthropic' && c && c.input_tokens === null) {
        shape(c,fields); safe(c.total_tokens);
        for (var i = 0; i < fields.length; i++)
            if (fields[i] !== 'total_tokens') requireHistory(c[fields[i]] === null);
    } else counts(c);
}
function localCell(c) {
    shape(c,['input_tokens','output_tokens','total_tokens']);
    safe(c.input_tokens); safe(c.output_tokens);
    requireHistory(c.total_tokens === plus(c.input_tokens,c.output_tokens));
}
function emptyCell() {
    var o = {}; for (var i = 0; i < fields.length; i++) o[fields[i]] = null; return o;
}
function addKnown(sum, cell) {
    for (var i = 0; i < fields.length; i++) {
        var name = fields[i];
        if (cell[name] !== undefined && cell[name] !== null) sum[name] = sum[name] === null ? cell[name] : plus(sum[name],cell[name]);
    }
}
function validateRollup(data) {
    shape(data,['contract','schemaVersion','readOnly','timezone','today','cloudFeed','sources','days','observedAt','validUntil']);
    requireHistory(data.contract === 'omarchy-unified-token-rollup/1' && data.schemaVersion === 1 && data.readOnly === true);
    requireHistory(typeof data.timezone === 'string' && data.timezone.length <= 128 && /^[A-Za-z0-9_+-]+(?:\/[A-Za-z0-9_+-]+)*$/.test(data.timezone));
    calendar(data.today,'day');
    requireHistory(instant(data.observedAt) <= instant(data.validUntil));
    requireHistory(data.cloudFeed !== null && typeof data.cloudFeed === 'object' && !Array.isArray(data.cloudFeed)
                   && ['admitted','stale','missing','unusable','future','misaligned'].indexOf(data.cloudFeed.state) >= 0);
    requireHistory(Array.isArray(data.sources) && data.sources.length > 0 && data.sources.length <= providers.length + 1);
    var seen = Object.create(null), cloud = Object.create(null), cloudCount = 0, locals = 0;
    for (var i = 0; i < data.sources.length; i++) {
        var source = data.sources[i];
        shape(source,['id','kind','provider']);
        requireHistory(typeof source.id === 'string' && /^[a-f0-9]{64}$/.test(source.id) && !seen[source.id]);
        seen[source.id] = true;
        if (source.kind === 'local') { requireHistory(source.provider === 'local'); locals++; }
        else {
            // Only cloud routes may be summed with router samples: this is the double-count boundary.
            requireHistory(source.kind === 'cloud' && providers.indexOf(source.provider) >= 0 && !cloud[source.provider]);
            cloud[source.provider] = true; cloudCount++;
        }
    }
    requireHistory(locals === 1);
    requireHistory(data.days !== null && typeof data.days === 'object' && !Array.isArray(data.days));
    var keys = Object.keys(data.days), previous = '';
    requireHistory(keys.length <= rollupDays);
    for (var d = 0; d < keys.length; d++) {
        var key = keys[d], day = data.days[key];
        calendar(key,'day');
        requireHistory(key > previous && key <= data.today); previous = key;
        shape(day,['total','byModel','byProvider','coverage']);
        shape(day.coverage,['partial','local','cloud']);
        var local = day.coverage.local, cover = day.coverage.cloud, total = 0;
        requireHistory(typeof day.coverage.partial === 'boolean' && (local !== null || cover !== null));
        requireHistory(day.byModel !== null && typeof day.byModel === 'object' && !Array.isArray(day.byModel));
        requireHistory(day.byProvider !== null && typeof day.byProvider === 'object' && !Array.isArray(day.byProvider));
        var models = Object.keys(day.byModel), rows = Object.keys(day.byProvider);
        requireHistory(models.length <= 256 && (local !== null || models.length === 0) && (cover !== null || rows.length === 0));
        for (var m = 0; m < models.length; m++) { localCell(day.byModel[models[m]]); total = plus(total,day.byModel[models[m]].total_tokens); }
        if (local !== null) {
            shape(local,['partial','basis','first','last','coveredSeconds','gapSeconds','daySeconds']);
            requireHistory(typeof local.partial === 'boolean' && (local.basis === 'collector' || local.basis === 'samples'));
            requireHistory(instant(local.first) <= instant(local.last));
            safe(local.coveredSeconds); safe(local.gapSeconds); safe(local.daySeconds);
        }
        if (cover !== null) {
            shape(cover,['partial','coveredSeconds','daySeconds','byProvider']);
            requireHistory(typeof cover.partial === 'boolean' && rows.length > 0);
            safe(cover.coveredSeconds); safe(cover.daySeconds);
            shape(cover.byProvider,rows);
        }
        for (var r = 0; r < rows.length; r++) {
            requireHistory(cloud[rows[r]] === true);
            rollupCloudCell(day.byProvider[rows[r]], rows[r]);
            total = plus(total,day.byProvider[rows[r]].total_tokens);
            var cell = cover.byProvider[rows[r]];
            shape(cell,['coveredSeconds','partial']); safe(cell.coveredSeconds);
            requireHistory(typeof cell.partial === 'boolean');
        }
        requireHistory(day.total === total);
        requireHistory(day.coverage.partial === (local === null || local.partial || (cover !== null ? cover.partial : cloudCount > 0)));
    }
}
function parseRollup(content) {
    var bad = {state:'malformed',summaries:[],bars:[],maximum:0,help:'Unsupported or malformed private history; no totals inferred.'};
    try {
        requireHistory(typeof content === 'string' && content.length > 0 && content.length <= 4194304);
        uniqueJson(content);
        var data = JSON.parse(content); validateRollup(data);
        var sources = [], byKind = Object.create(null), cloudDaily = Object.create(null), today = data.today;
        for (var i = 0; i < data.sources.length; i++) {
            var s = data.sources[i];
            byKind[s.kind === 'local' ? 'local' : s.provider] = s.id;
            if (s.kind !== 'local') cloudDaily[s.provider] = Object.create(null);
            sources.push({id:s.id, provider:s.provider, profile:s.kind === 'local' ? 'router' : 'hermes', present:true,
                          coverage:{events:{delta:0, measured_zero:0, gap:0}, observed:{total_tokens:0}}});
        }
        function source(id) { for (var n = 0; n < sources.length; n++) if (sources[n].id === id) return sources[n]; }
        var periods = {day:{}, week:{}, month:{}, year:{}}, keys = Object.keys(data.days);
        function add(key, id, cell, partial) {
            var names = {day:key, week:mondayOf(key), month:key.slice(0,7), year:key.slice(0,4)};
            for (var g = 0; g < grains.length; g++) {
                var period = periods[grains[g]], name = names[grains[g]];
                if (!period[name]) period[name] = {order:[], cells:{}};
                var row = period[name].cells[id];
                if (!row) { row = period[name].cells[id] = {id:id, counts:emptyCell(), partial:false, days:0}; period[name].order.push(id); }
                addKnown(row.counts,cell); row.partial = row.partial || partial; row.days++;
            }
        }
        for (var d = 0; d < keys.length; d++) {
            var key = keys[d], day = data.days[key], names = Object.keys(day.byProvider);
            for (var p = 0; p < names.length; p++) {
                var row = day.byProvider[names[p]], id = byKind[names[p]], info = source(id);
                add(key,id,row,day.coverage.cloud.byProvider[names[p]].partial);
                cloudDaily[names[p]][key] = row.total_tokens;
                info.coverage.events[row.total_tokens > 0 ? 'delta' : 'measured_zero']++;
                info.coverage.observed.total_tokens = plus(info.coverage.observed.total_tokens,row.total_tokens);
            }
            if (day.coverage.local !== null) {
                var local = {input_tokens:0, output_tokens:0, total_tokens:0}, models = Object.keys(day.byModel);
                for (var m = 0; m < models.length; m++) addKnown(local,day.byModel[models[m]]);
                var info = source(byKind.local);
                add(key,byKind.local,local,day.coverage.local.partial);
                info.coverage.events[local.total_tokens > 0 ? 'delta' : 'measured_zero']++;
                info.coverage.observed.total_tokens = plus(info.coverage.observed.total_tokens,local.total_tokens);
            }
        }
        var buckets = {}, measured = 0, observed = 0;
        for (var g = 0; g < grains.length; g++) {
            var grain = grains[g], names = Object.keys(periods[grain]);
            buckets[grain] = [];
            for (var b = 0; b < names.length; b++) {
                var period = periods[grain][names[b]], cells = [];
                for (var c = 0; c < period.order.length; c++) {
                    var cell = period.cells[period.order[c]];
                    cells.push({id:cell.id, counts:cell.counts,
                                partial:cell.partial || cell.days < elapsedPeriodDays(names[b],grain,today)});
                }
                buckets[grain].push({key:names[b], sources:cells});
            }
        }
        for (var i = 0; i < sources.length; i++) {
            measured = plus(measured,plus(sources[i].coverage.events.delta,sources[i].coverage.events.measured_zero));
            observed = plus(observed,sources[i].coverage.observed.total_tokens);
        }
        var state = measured === 0 ? 'baseline' : observed === 0 ? 'zero' : 'observed';
        return {state:state, summaries:[], bars:[], maximum:0, missingHistory:true,
                data:{timezone:data.timezone, sources:sources, buckets:buckets},
                rollup:{today:today, observedAtMs:instant(data.observedAt), validUntilMs:instant(data.validUntil), cloud:cloudDaily},
                help:'Dated Cloud + Local totals in '+data.timezone+' as of '+data.observedAt+'. Cloud: sampled Hermes deltas; undated baselines are not included (feed '+data.cloudFeed.state+'). Local: sampled llama.cpp evaluated input + generated output; cached input is not counted, so Cloud and Local totals are not the same kind of count. Hermes requests sent to Local are counted once, by the router. ? means no record for that date or metric; 0 is measured; + is a lower bound because part of the period or a source was not recorded. Not full request accounting.'};
    } catch (error) { return bad; }
}
// Whether the published "today" still names today. Times are absolute, so the
// consumer needs no timezone rules; a stopped publisher simply expires.
function rollupFresh(report, nowMs) {
    var dated = report ? report.rollup : null;
    return !!dated && typeof nowMs === 'number' && isFinite(nowMs)
        && nowMs < dated.validUntilMs && dated.observedAtMs <= nowMs + rollupClockSlack;
}
// One provider's dated Cloud windows for the bar: exactly today, never the
// latest retained date, and the seven calendar days ending today. Pass only a
// report rollupFresh admitted.
function rollupCloudWindows(report, provider) {
    var days = report.rollup.cloud[provider], out = {today:null, week:null};
    if (!days) return out;
    var today = calendar(report.rollup.today,'day'), sum = 0, known = 0;
    for (var j = 0; j < 7; j++) {
        var value = days[isoDay(today - j * dayMs)];
        if (value === undefined) continue;
        if (j === 0) out.today = value;
        sum = plus(sum,value); known++;
    }
    if (known) out.week = sum;
    return out;
}

// Clock-anchored aggregate API. The canonical parser remains authoritative;
// this view never mutates history, manufactures request events or adds lifetime totals.
// Work is bounded by the admitted 512-source history and 366 Local date rows.
function selectDatedHistory(report, local, clock, options, nowMs) {
    var out = {state:'unavailable', grain:'day', metric:'total_tokens', mode:'combined',
        periods:{day:null,week:null,month:null,year:null}, summaries:[],bars:[],maximum:0,
        selectionError:true,selectionEmpty:false,lifetime:null,sources:[],selectedSources:[],
        coverage:{cloudKnown:false,sourcePopulationKnown:false},
        undatedCloud:{value:null,metric:'total_tokens'},recordedCloud:{value:null,metric:'total_tokens'},
        help:'Dated windows use the shared clock. Cloud is sampled canonical deltas; Local is sampled evaluated input + output. Missing is unknown, 0 is measured. + is a lower bound. Undated snapshots are not lifetime usage.'};
    try {
        requireHistory(typeof nowMs==='number' && isFinite(nowMs));
        shape(clock,['state','timezone','observedAt','expiresAt','keys','localCalendarAligned']);
        requireHistory(clock.state==='ready' && typeof clock.localCalendarAligned==='boolean');
        var zone=clock.timezone;
        requireHistory(typeof zone==='string' && zone.length>0 && zone.length<=128
            && /^[A-Za-z0-9_+-]+(?:\/[A-Za-z0-9_+-]+)*$/.test(zone)
            && zone!=='localtime' && zone!=='posixrules' && zone.indexOf('right/')!==0 && zone.indexOf('posix/')!==0);
        var observed=instant(clock.observedAt), expires=instant(clock.expiresAt);
        // Normal publications use the helper's <=45s lease. Accept explicit
        // offline clock leases only up to one day; never extend their expiry.
        requireHistory(expires>observed && expires-observed<=dayMs && nowMs>=observed && nowMs<expires);
        shape(clock.keys,grains);
        for (var g=0;g<grains.length;g++) calendar(clock.keys[grains[g]],grains[g]);
        requireHistory(clock.keys.week===mondayOf(clock.keys.day)
            && clock.keys.month===clock.keys.day.slice(0,7) && clock.keys.year===clock.keys.day.slice(0,4));
        if (options===null || options===undefined) options={};
        requireHistory(typeof options==='object' && !Array.isArray(options));
        var optionKeys=Object.keys(options);
        for (var o=0;o<optionKeys.length;o++) requireHistory(['metric','grain','mode'].indexOf(optionKeys[o])>=0);
        var metric=options.metric===undefined?'total_tokens':options.metric;
        var grain=options.grain===undefined?'day':options.grain;
        requireHistory(fields.indexOf(metric)>=0 && grains.indexOf(grain)>=0
            && (options.mode===undefined || options.mode==='combined'));
        out.metric=metric;out.grain=grain;
        out.undatedCloud.metric=metric;out.recordedCloud.metric=metric;
        var data=report && report.data ? report.data : null;
        // Withhold future Cloud before comparing its timezone or reading any counts.
        if (data && instant(data.observedAt)>nowMs) data=null;
        if (data) requireHistory(data.timezone===zone);
        var cloudKnown=!!data, cloudSources=data?data.sources.length:0;
        out.coverage={cloudKnown:cloudKnown,sourcePopulationKnown:cloudKnown};
        var indexed={day:Object.create(null),week:Object.create(null),month:Object.create(null),year:Object.create(null)};
        if (data) {
            var snapshot=0, recorded=0, measured=0;
            for (var s=0;s<data.sources.length;s++) {
                var source=data.sources[s];snapshot=plus(snapshot,source.counts[metric]);
                out.sources.push({id:source.id,provider:source.provider,present:source.present,
                    label:source.provider+' · '+source.profile+' · '+source.id.slice(0,8)});
                out.selectedSources.push(source.id);
                var events=source.coverage.events;
                if (plus(plus(events.delta,events.measured_zero),events.gap)>0) {
                    recorded=plus(recorded,source.coverage.observed[metric]);measured++;
                }
            }
            out.undatedCloud.value=data.sources.length?snapshot:null;
            out.recordedCloud.value=measured?recorded:null;
            for (var g=0;g<grains.length;g++) {
                var name=grains[g], buckets=data.buckets[name];
                for (var b=0;b<buckets.length;b++) indexed[name][buckets[b].key]=buckets[b];
            }
        }
        // Invalid optional Local input is withheld as a whole, not partially summed.
        var dates=Object.create(null), localValid=false;
        if (metric==='total_tokens' && clock.localCalendarAligned && local && local.id==='local'
            && local.readOnly===true && local.hasLocalStats===true) {
            try {
                requireHistory(Array.isArray(local.recentDays) && local.recentDays.length<=366);
                var seen=Object.create(null), candidate=Object.create(null);
                for (var d=0;d<local.recentDays.length;d++) {
                    var row=local.recentDays[d];shape(row,['date','messageCount','recorded']);calendar(row.date,'day');
                    requireHistory(row.date<=clock.keys.day && !seen[row.date] && typeof row.recorded==='boolean');seen[row.date]=true;
                    if (row.recorded) candidate[row.date]=safe(row.messageCount);
                    else requireHistory(row.messageCount===null);
                }
                dates=candidate;localValid=true;
            } catch (invalidLocal) { dates=Object.create(null); }
        }
        var dateKeys=Object.keys(dates);
        function period(name,key) {
            var cloudValue=0, cloudCount=0, cloudPartial=false, bucket=indexed[name][key];
            if (bucket) for (var c=0;c<bucket.sources.length;c++) {
                var cell=bucket.sources[c], value=cell.counts[metric];
                if (value===null) continue;
                cloudValue=plus(cloudValue,value);cloudCount++;cloudPartial=cloudPartial || cell.partial===true;
            }
            var localValue=0, recordedDays=0, localSources=metric==='total_tokens'?1:0;
            var elapsed=elapsedPeriodDays(key,name,clock.keys.day);
            if (localValid) for (var d=0;d<dateKeys.length;d++) {
                var day=dateKeys[d], periodKey=name==='day'?day:name==='week'?mondayOf(day):day.slice(0,name==='month'?7:4);
                if (periodKey===key) {localValue=plus(localValue,dates[day]);recordedDays++;}
            }
            var cloud={value:cloudCount?cloudValue:null,sources:cloudKnown?cloudSources:null,
                knownSources:cloudCount,partial:!cloudKnown || cloudPartial || cloudCount<cloudSources};
            var sampled={value:recordedDays?localValue:null,sources:localSources,knownSources:recordedDays?1:0,
                recordedDays:recordedDays,elapsedDays:elapsed,partial:localSources>0 && recordedDays<elapsed};
            var known=cloudCount+sampled.knownSources;
            return {key:key,grain:name,value:known?plus(cloudCount?cloudValue:0,recordedDays?localValue:0):null,
                cloud:cloud,local:sampled,known:known,knownSources:known,sources:cloudKnown?cloudSources+localSources:null,
                partial:cloud.partial || sampled.partial,lowerBound:true,series:[]};
        }
        var current={};
        for (var g=0;g<grains.length;g++) {var name=grains[g];current[name]=period(name,clock.keys[name]);}
        var bars=[], maximum=0, count=grain==='year'?5:7;
        for (var j=count-1;j>=0;j--) {
            var date=new Date(calendar(clock.keys[grain],grain));
            if (grain==='day' || grain==='week') date.setUTCDate(date.getUTCDate()-j*(grain==='week'?7:1));
            else if (grain==='month') date.setUTCMonth(date.getUTCMonth()-j);
            else date.setUTCFullYear(date.getUTCFullYear()-j);
            var key=date.toISOString().slice(0,grain==='year'?4:grain==='month'?7:10);
            var cell=j===0?current[grain]:period(grain,key);bars.push(cell);
            if (cell.value!==null) maximum=Math.max(maximum,cell.value);
        }
        out.periods=current;out.bars=bars;out.maximum=maximum;
        for (var g=0;g<grains.length;g++) out.summaries.push(current[grains[g]]);
        out.selectionError=false;
        var known=false, positive=false;
        for (var g=0;g<grains.length;g++) {var value=current[grains[g]].value;known=known || value!==null;positive=positive || value>0;}
        out.state=positive?'observed':known?'zero':out.undatedCloud.value!==null?'baseline':'unavailable';
        return out;
    } catch (invalidSelection) {
        out.periods={day:null,week:null,month:null,year:null};out.summaries=[];out.bars=[];out.maximum=0;
        out.sources=[];out.selectedSources=[];out.selectionError=true;return out;
    }
}
