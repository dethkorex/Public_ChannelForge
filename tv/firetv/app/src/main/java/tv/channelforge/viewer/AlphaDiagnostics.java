package tv.channelforge.viewer;

import android.content.Context;
import android.util.AtomicFile;
import org.json.JSONException;
import org.json.JSONObject;
import javax.net.ssl.HttpsURLConnection;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

final class AlphaDiagnostics {
    private static final String ENDPOINT = "https://channelforge-directory.fresh-anorak.workers.dev/v1/diagnostics/exit";
    private static final String[] METRICS = ("sdk firstFrameMs width height postStartRebuffers postStartRebufferMs retries "
            + "playerErrorCode httpError segment_loads segment_errors manifest_errors slowest_segment_ms slowest_manifest_ms "
            + "videoBitrateBps renderedFrames droppedFrames bandwidthEstimateBps segment_rate_bps relay_cache_hits relay_cache_misses "
            + "kind delivery_path delivery_route").split(" ");
    private static final Object FILE_LOCK = new Object();
    private final AtomicFile pending;

    AlphaDiagnostics(Context context) {
        pending = new AtomicFile(new File(context.getFilesDir(), "alpha-health.json"));
    }

    void submit(JSONObject metrics, Throwable crash) {
        JSONObject report = new JSONObject();
        try {
            report.put("platform", ViewerApplication.platform()).put("component", "tv").put("app", BuildConfig.VERSION_NAME)
                    .put("report_type", crash == null ? "exit" : "crash");
            if (crash != null) report.put("crash_kind", crash.getClass().getSimpleName());
            if (metrics != null) for (String key : METRICS) {
                Object value = metrics.opt(key);
                if (value instanceof Number || value instanceof String) report.put(key, value);
            }
        } catch (JSONException invalid) { return; }
        synchronized (FILE_LOCK) {
            FileOutputStream output = null;
            try {
                output = pending.startWrite();
                output.write(report.toString().getBytes(StandardCharsets.UTF_8));
                pending.finishWrite(output);
            } catch (IOException failure) {
                if (output != null) pending.failWrite(output);
                return;
            }
        }
        if (crash == null) flush();
    }

    void flush() {
        if (!pending.getBaseFile().isFile()) return;
        new Thread(() -> {
            HttpsURLConnection connection = null;
            try {
                byte[] data;
                synchronized (FILE_LOCK) {data = pending.readFully();}
                connection = (HttpsURLConnection) new URL(ENDPOINT).openConnection();
                connection.setInstanceFollowRedirects(false);
                connection.setConnectTimeout(2500);
                connection.setReadTimeout(2500);
                connection.setRequestMethod("POST");
                connection.setRequestProperty("Content-Type", "application/json");
                connection.setFixedLengthStreamingMode(data.length);
                connection.setDoOutput(true);
                try (java.io.OutputStream body = connection.getOutputStream()) {body.write(data);}
                if (connection.getResponseCode() == 204) synchronized (FILE_LOCK) {
                    if (Arrays.equals(data, pending.readFully())) pending.delete();
                }
            } catch (IOException ignored) {
                // The next launch retries the most recent unsent report.
            } finally {
                if (connection != null) connection.disconnect();
            }
        }, "alpha-health-report").start();
    }
}
