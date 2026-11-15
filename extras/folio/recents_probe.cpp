// Synthetic native regression: never opens the user's history or settings.
#include <gio/gio.h>
#include <QGuiApplication>
#include <QTemporaryDir>
#include <QFile>
#include <QDir>
#include <QUrl>
#include <QJsonDocument>
#include <QJsonObject>
#include <QCryptographicHash>
#include <sys/inotify.h>
#include <unistd.h>
#include <cerrno>
#include <cstring>
#include <cstdio>
#include "foldermodel.h"
#include "places.h"

struct Observation { int rows; bool sidebar; int opens; };

static Observation observe(GSettings *settings, bool enabled, const QString &home,
                           const QString &xbel) {
    if (!g_settings_set_boolean(settings, "remember-recent-files", enabled)
        || bool(g_settings_get_boolean(settings, "remember-recent-files")) != enabled)
        qFatal("Synthetic memory setting did not reach requested value");
    const int fd = inotify_init1(IN_NONBLOCK | IN_CLOEXEC);
    if (fd < 0 || inotify_add_watch(fd, QFile::encodeName(xbel).constData(), IN_OPEN) < 0)
        qFatal("Synthetic file-read observer unavailable");
    QString error;
    const auto rows = FolderModel::list(FolderModel::recentPath(), false, "name", &error);
    if (!error.isEmpty()) qFatal("Synthetic recents listing unexpectedly errored");
    Places places(home);
    bool sidebar = false;
    for (const auto &row : places.views())
        sidebar |= row.toMap().value("path").toString() == FolderModel::recentPath();
    int opens = 0;
    alignas(inotify_event) char data[4096];
    for (;;) {
        const ssize_t n = ::read(fd, data, sizeof(data));
        if (n < 0 && errno == EAGAIN) break;
        if (n <= 0) qFatal("Synthetic file-read observer failed");
        for (ssize_t at = 0; at < n;) {
            const auto *event = reinterpret_cast<const inotify_event *>(data + at);
            if (event->mask & IN_OPEN) ++opens;
            at += sizeof(inotify_event) + event->len;
        }
    }
    ::close(fd);
    return {int(rows.size()), sidebar, opens};
}

int main(int argc, char **argv) {
    if (std::strcmp(qgetenv("GSETTINGS_BACKEND").constData(), "memory") != 0)
        qFatal("Only synthetic memory GSettings admitted");
    QGuiApplication app(argc, argv);
    QTemporaryDir fixture;
    if (!fixture.isValid()) qFatal("Private fixture creation failed");
    qputenv("XDG_DATA_HOME", QFile::encodeName(fixture.path()));
    QFile item(fixture.filePath("synthetic.txt"));
    if (!item.open(QIODevice::WriteOnly) || item.write("synthetic fixture\n") < 0)
        qFatal("Synthetic file creation failed");
    item.close();
    const QString xbel = fixture.filePath("recently-used.xbel");
    const QByteArray bytes = "<?xml version=\"1.0\"?><xbel version=\"1.0\"><bookmark href=\""
        + QUrl::fromLocalFile(item.fileName()).toEncoded()
        + "\" modified=\"2026-01-01T00:00:00Z\" visited=\"2026-01-01T00:00:00Z\"/></xbel>";
    QFile history(xbel);
    if (!history.open(QIODevice::WriteOnly) || history.write(bytes) != bytes.size())
        qFatal("Synthetic XBEL creation failed");
    history.close();
    auto *source = g_settings_schema_source_get_default();
    auto *schema = source ? g_settings_schema_source_lookup(source, "org.gnome.desktop.privacy", TRUE) : nullptr;
    if (!schema || !g_settings_schema_has_key(schema, "remember-recent-files"))
        qFatal("Required installed schema metadata unavailable");
    auto *settings = g_settings_new_full(schema, nullptr, nullptr);
    const auto enabled = observe(settings, true, fixture.path(), xbel);
    const auto disabled = observe(settings, false, fixture.path(), xbel);
    const auto restored = observe(settings, true, fixture.path(), xbel);
    if (!history.open(QIODevice::ReadOnly)) qFatal("Synthetic XBEL readback failed");
    const bool same = history.readAll() == bytes;
    history.close();
    g_object_unref(settings);
    g_settings_schema_unref(schema);
    const bool control = enabled.rows == 1 && enabled.sidebar && enabled.opens > 0
                      && restored.rows == 1 && restored.sidebar && restored.opens > 0;
    const bool supported = disabled.rows == 0 && !disabled.sidebar && disabled.opens == 0;
    const QJsonObject result{{"synthetic_only", true}, {"healthy_controls", control},
        {"enabled_rows", enabled.rows}, {"enabled_xbel_opens", enabled.opens},
        {"disabled_rows", disabled.rows}, {"disabled_sidebar", disabled.sidebar},
        {"disabled_xbel_opens", disabled.opens}, {"restored_rows", restored.rows},
        {"history_bytes_unchanged", same}, {"native_privacy_supported", supported}};
    const auto output = QJsonDocument(result).toJson(QJsonDocument::Compact);
    std::printf("%s\n", output.constData());
    return control && same && supported ? 0 : 1;
}
