package tv.channelforge.viewer;

import android.app.Application;
import android.os.Build;
import java.io.File;

public final class ViewerApplication extends Application {
    @Override public void onCreate() {
        super.onCreate();
        long staleBefore = System.currentTimeMillis() - 24L * 60 * 60 * 1000;
        File[] downloads = getCacheDir().listFiles((dir, name) -> name.matches("channel-forge-update-[0-9]+\\.apk"));
        if (downloads != null) for (File download : downloads) {
            if (download.isFile() && download.lastModified() < staleBefore) download.delete();
        }
        AlphaDiagnostics reports = new AlphaDiagnostics(this);
        reports.flush();
        Thread.UncaughtExceptionHandler previous = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler((thread, error) -> {
            try { reports.submit(null, error); }
            finally { if (previous != null) previous.uncaughtException(thread, error); }
        });
    }

    static String platform() {
        return "Amazon".equalsIgnoreCase(Build.MANUFACTURER) ? "fire-tv" : "android";
    }
}
