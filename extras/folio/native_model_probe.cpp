// Real Folio model/cache regression; only private synthetic data is admitted.
#define main originalReaderProbeMain
#include "recents_probe.cpp"
#undef main
#include <QElapsedTimer>
#include <QRegularExpression>
#include <QThread>
#include <QThreadPool>
#include <sys/stat.h>

static bool settled(FolderModel &model, int rows) {
    QElapsedTimer time;
    time.start();
    while (time.elapsed() < 5000) {
        QCoreApplication::processEvents();
        if (!model.loading() && model.count() == rows)
            return true;
        QThread::msleep(2);
    }
    return false;
}

static void policy(GSettings *settings, bool enabled) {
    if (!g_settings_set_boolean(settings, "remember-recent-files", enabled)
        || bool(g_settings_get_boolean(settings, "remember-recent-files")) != enabled)
        qFatal("Synthetic policy readback failed");
}

// Observe only this private PID namespace's own inotify watch metadata. This
// does not read XBEL. The enabled control proves the watch observer is live.
static int historyWatches(const QString &xbel) {
    struct stat target;
    if (::stat(QFile::encodeName(xbel).constData(), &target) != 0)
        qFatal("Synthetic history metadata unavailable");
    int count = 0;
    const QRegularExpression pattern(QStringLiteral("inotify wd:[0-9a-f]+ ino:([0-9a-f]+)"));
    for (const auto &name : QDir("/proc/self/fdinfo").entryList(QDir::Files)) {
        QFile info("/proc/self/fdinfo/" + name);
        if (!info.open(QIODevice::ReadOnly))
            continue;
        auto matches = pattern.globalMatch(QString::fromUtf8(info.readAll()));
        while (matches.hasNext()) {
            bool ok = false;
            const auto inode = matches.next().captured(1).toULongLong(&ok, 16);
            if (ok && inode == static_cast<qulonglong>(target.st_ino))
                ++count;
        }
    }
    return count;
}

int main(int argc, char **argv) {
    if (qgetenv("GSETTINGS_BACKEND") != "memory")
        qFatal("Only synthetic memory GSettings admitted");
    QGuiApplication app(argc, argv);
    QTemporaryDir fixture;
    if (!fixture.isValid()) qFatal("Private fixture creation failed");
    qputenv("XDG_DATA_HOME", QFile::encodeName(fixture.path()));
    const auto ordinary = fixture.filePath("ordinary");
    if (!QDir().mkpath(ordinary)) qFatal("Private folder creation failed");
    QFile item(fixture.filePath("synthetic.txt"));
    if (!item.open(QIODevice::WriteOnly) || item.write("synthetic fixture\n") < 0)
        qFatal("Synthetic file creation failed");
    item.close();
    const auto xbel = fixture.filePath("recently-used.xbel");
    const QByteArray bytes = "<?xml version=\"1.0\"?><xbel version=\"1.0\"><bookmark href=\""
        + QUrl::fromLocalFile(item.fileName()).toEncoded()
        + "\" visited=\"2026-01-01T00:00:00Z\"/></xbel>";
    QFile history(xbel);
    if (!history.open(QIODevice::WriteOnly) || history.write(bytes) != bytes.size())
        qFatal("Synthetic history creation failed");
    history.close();
    auto *source = g_settings_schema_source_get_default();
    auto *schema = source ? g_settings_schema_source_lookup(source, "org.gnome.desktop.privacy", TRUE) : nullptr;
    if (!schema || !g_settings_schema_has_key(schema, "remember-recent-files"))
        qFatal("Required schema metadata unavailable");
    auto *settings = g_settings_new_full(schema, nullptr, nullptr);
    policy(settings, true);
    FolderModel model;
    model.setPath(FolderModel::recentPath());
    const bool warm = settled(model, 1);
    const int enabledWatches = historyWatches(xbel);
    model.setPath(ordinary);
    const bool ordinaryWorks = settled(model, 0);
    policy(settings, false);
    int peak = 0;
    QObject::connect(&model, &FolderModel::countChanged, [&] { peak = qMax(peak, model.count()); });
    model.setPath(FolderModel::recentPath());
    const int cachedRows = model.count();
    const int cachedPeak = peak;
    const bool blockedSettled = settled(model, 0);
    const int disabledWatches = historyWatches(xbel);
    const bool lowerBlocked = FolderModel::listRecent(xbel, false).isEmpty();

    policy(settings, true);
    model.reload();
    const bool rewarmed = settled(model, 1);
    model.setQuery("synthetic");
    const bool queried = settled(model, 1);
    policy(settings, false);
    peak = 0;
    model.setQuery("");
    const int queryPeak = peak;
    const bool querySettled = settled(model, 0);

    policy(settings, true);
    model.reload();
    const bool reloadWarm = settled(model, 1);
    policy(settings, false);
    model.reload();
    const int reloadRows = model.count();
    const bool reloadSettled = settled(model, 0);

    // Let the real worker finish before disabling; hold its UI callback until
    // afterward. A reader-only guard cannot prevent this late publication.
    policy(settings, true);
    model.setPath(ordinary);
    const bool ordinaryAgain = settled(model, 0);
    model.setPath(FolderModel::recentPath());
    const bool workerFinished = QThreadPool::globalInstance()->waitForDone(5000);
    const bool callbackPending = model.loading();
    policy(settings, false);
    QElapsedTimer time;
    time.start();
    while (model.loading() && time.elapsed() < 5000) {
        QCoreApplication::processEvents();
        QThread::msleep(2);
    }
    const int lateRows = model.count();

    policy(settings, true);
    int parserHookCalls = 0;
    const auto lateParsed = FolderModel::listRecent(xbel, false, 200, [&] {
        ++parserHookCalls;
        policy(settings, false);
        return false;
    });
    policy(settings, true);
    model.setPath(ordinary);
    const bool ordinaryRestored = settled(model, 0);
    model.setPath(FolderModel::recentPath());
    const bool restored = settled(model, 1);
    const int restoredWatches = historyWatches(xbel);
    if (!history.open(QIODevice::ReadOnly)) qFatal("Synthetic history readback failed");
    const bool same = history.readAll() == bytes;
    history.close();
    const bool controls = warm && ordinaryWorks && rewarmed && queried && reloadWarm
        && ordinaryAgain && workerFinished && callbackPending && ordinaryRestored && restored
        && enabledWatches > 0 && restoredWatches > 0 && parserHookCalls > 0;
    const bool supported = cachedRows == 0 && cachedPeak == 0 && disabledWatches == 0
        && lowerBlocked && queryPeak == 0 && reloadRows == 0 && lateRows == 0
        && lateParsed.isEmpty() && blockedSettled && querySettled && reloadSettled;
    const QJsonObject result{{"synthetic_only", true}, {"healthy_controls", controls},
        {"history_bytes_unchanged", same}, {"native_privacy_supported", supported},
        {"disabled_cached_rows", cachedRows}, {"disabled_cached_peak", cachedPeak},
        {"disabled_query_peak", queryPeak}, {"disabled_reload_rows", reloadRows},
        {"disabled_watches", disabledWatches}, {"enabled_watches", enabledWatches},
        {"restored_watches", restoredWatches}, {"late_callback_rows", lateRows},
        {"late_parser_rows", int(lateParsed.size())}, {"parser_hook_calls", parserHookCalls}};
    g_object_unref(settings);
    g_settings_schema_unref(schema);
    std::printf("%s\n", QJsonDocument(result).toJson(QJsonDocument::Compact).constData());
    return controls && same && supported ? 0 : 1;
}
