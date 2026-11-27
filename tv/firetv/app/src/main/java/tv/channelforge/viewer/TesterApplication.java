package tv.channelforge.viewer;

import android.app.Application;
import android.os.Build;
import android.util.AtomicFile;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import javax.net.ssl.HttpsURLConnection;
import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

public final class TesterApplication extends Application {
    private static final String PREFERENCES = "channel_forge_tv";
    private static final String AUTO_REPORTS = "automatic_crash_reports";
    static final String TESTER_CONSENT = "tester_diagnostics_opt_in";
    private AtomicFile pending;
    private volatile String latestTimeline = "[]";

    @Override public void onCreate() {
        super.onCreate();
        if (!BuildConfig.TESTER_BUILD) return;
        pending = new AtomicFile(new File(getFilesDir(), "tester-crash.json"));
        Thread.UncaughtExceptionHandler previous = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler((thread, error) -> {
            try {
                if (getSharedPreferences(PREFERENCES, MODE_PRIVATE).getBoolean(TESTER_CONSENT, false)) capture(error);
            } finally {
                if (previous != null) previous.uncaughtException(thread, error);
            }
        });
    }

    boolean automaticReports() {
        return getSharedPreferences(PREFERENCES, MODE_PRIVATE).getBoolean(AUTO_REPORTS, false);
    }

    void setAutomaticReports(boolean enabled) {
        getSharedPreferences(PREFERENCES, MODE_PRIVATE).edit().putBoolean(AUTO_REPORTS, enabled).apply();
    }

    synchronized void clearSavedCrash() {
        if (pending != null) pending.delete();
        latestTimeline = "[]";
    }

    void rememberTimeline(JSONArray timeline) { latestTimeline = timeline.toString(); }

    static String platform() {
        return "Amazon".equalsIgnoreCase(Build.MANUFACTURER) ? "fire-tv" : "android";
    }

    private synchronized void capture(Throwable error) {
        FileOutputStream output = null;
        try {
            Throwable cause = error.getCause() == null ? error : error.getCause();
            String kind = cause.getClass().getSimpleName();
            if (!kind.matches("OutOfMemoryError|IllegalStateException|NullPointerException|IllegalArgumentException"
                    + "|SecurityException|IndexOutOfBoundsException|IOException|Error")) {
                kind = "Exception";
            }
            int line = 0;
            for (StackTraceElement frame : cause.getStackTrace()) {
                if (frame.getClassName().startsWith("tv.channelforge.viewer.")) {
                    line = Math.max(0, frame.getLineNumber());
                    break;
                }
            }
            JSONObject report = new JSONObject().put("platform", platform()).put("app", BuildConfig.VERSION_NAME)
                    .put("build_revision", BuildConfig.VERSION_CODE)
                    .put("sdk", Build.VERSION.SDK_INT).put("model", Build.MODEL).put("report_type", "crash")
                    .put("crash_area", "viewer").put("crash_kind", kind).put("crash_line", line)
                    .put("timeline", new JSONArray(latestTimeline));
            output = pending.startWrite();
            output.write(report.toString().getBytes(StandardCharsets.UTF_8));
            pending.finishWrite(output);
        } catch (IOException | JSONException ignored) {
            if (output != null) pending.failWrite(output);
        }
    }

    void sendPendingCrash() {
        if (!automaticReports()) return;
        String report = savedCrash();
        if (report != null && automaticReports()) sendSavedCrash(report);
    }

    synchronized String savedCrash() {
        if (pending == null || !pending.getBaseFile().isFile()) return null;
        try {
            byte[] report = pending.readFully();
            return report.length <= 8192 ? new String(report, StandardCharsets.UTF_8) : null;
        } catch (IOException ignored) { return null; }
    }

    String sendSavedCrash(String report) {
        byte[] payload = report.getBytes(StandardCharsets.UTF_8);
        String receipt = post(payload);
        if (receipt == null) return null;
        try {
            synchronized (this) {
                if (pending != null && Arrays.equals(payload, pending.readFully())) pending.delete();
            }
        } catch (IOException ignored) {
        }
        return receipt;
    }

    static String post(byte[] body) {
        if (BuildConfig.TESTER_REPORT_URL.isEmpty() || body.length > 8192) return null;
        HttpsURLConnection connection = null;
        try {
            connection = (HttpsURLConnection) new URL(BuildConfig.TESTER_REPORT_URL).openConnection();
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(8000);
            connection.setReadTimeout(8000);
            connection.setRequestMethod("POST");
            connection.setRequestProperty("Content-Type", "application/json");
            connection.setDoOutput(true);
            connection.setFixedLengthStreamingMode(body.length);
            try (OutputStream output = connection.getOutputStream()) { output.write(body); }
            if (connection.getResponseCode() == 201) {
                try (BufferedReader input = new BufferedReader(new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8))) {
                    String receipt = input.readLine();
                    if (receipt != null && receipt.matches("[0-9a-f]{10}")) return receipt;
                }
            }
        } catch (IOException ignored) {
        } finally {
            if (connection != null) connection.disconnect();
        }
        return null;
    }
}
