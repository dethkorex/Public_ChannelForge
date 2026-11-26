package tv.channelforge.viewer;

import android.util.AtomicFile;
import android.util.Log;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Arrays;
import java.util.Locale;

/** Private, bounded snapshots. An interrupted write leaves the previous snapshot readable. */
final class GuideCache {
    private final File directory;
    private static final long ENTRY_LIMIT = 4L * 1024 * 1024;
    private static final long TOTAL_LIMIT = 20L * 1024 * 1024;
    private static final long MAX_AGE = 7L * 86400 * 1000;

    GuideCache(File files) { directory = new File(files, "guide-cache"); }

    private File file(String key) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(key.getBytes(StandardCharsets.UTF_8));
            StringBuilder name = new StringBuilder();
            for (byte b : digest) name.append(String.format(Locale.ROOT, "%02x", b));
            return new File(directory, name + ".json");
        } catch (NoSuchAlgorithmException e) { throw new AssertionError(e); }
    }

    synchronized String read(String key) {
        File file = file(key);
        AtomicFile atomic = new AtomicFile(file);
        try (java.io.FileInputStream input = atomic.openRead()) {
            long size = input.getChannel().size();
            if (size <= 0 || size > ENTRY_LIMIT || System.currentTimeMillis() - file.lastModified() > MAX_AGE) {
                atomic.delete();
                return "";
            }
            byte[] bytes = new byte[(int)size];
            new java.io.DataInputStream(input).readFully(bytes);
            return new String(bytes, StandardCharsets.UTF_8);
        } catch (java.io.FileNotFoundException absent) {
            return "";
        } catch (IOException unavailable) {
            Log.w("ChannelForgeGuide", "Snapshot read failed");
            return "";
        }
    }

    synchronized void write(String key, String json) {
        if (json.length() > ENTRY_LIMIT) return;
        byte[] data = json.getBytes(StandardCharsets.UTF_8);
        if (data.length > ENTRY_LIMIT) return;
        AtomicFile atomic = new AtomicFile(file(key));
        FileOutputStream stream = null;
        try {
            stream = atomic.startWrite();
            stream.write(data);
            atomic.finishWrite(stream);
        } catch (IOException unavailable) {
            if (stream != null) atomic.failWrite(stream);
            Log.w("ChannelForgeGuide", "Snapshot write failed");
            return;
        }
        File[] files = directory.listFiles((dir, name) -> name.endsWith(".json"));
        if (files == null) return;
        Arrays.sort(files, (left, right) -> Long.compare(right.lastModified(), left.lastModified()));
        long total = 0;
        for (int i = 0; i < files.length; i++) {
            File entry = files[i];
            total += entry.length();
            if (i >= 128 || total > TOTAL_LIMIT || System.currentTimeMillis() - entry.lastModified() > MAX_AGE)
                new AtomicFile(entry).delete();
        }
    }
}
