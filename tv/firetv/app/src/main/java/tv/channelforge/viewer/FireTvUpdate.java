package tv.channelforge.viewer;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.content.pm.PackageInstaller;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.os.Build;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import javax.net.ssl.HttpsURLConnection;

final class FireTvUpdate {
    private static final String APK = "ChannelForgeTV-FireTV.apk";
    private static final String CHECKSUM = "SHA256SUMS-TV.txt";
    private static final String RELEASE_ASSETS =
            "https://github.com/dethkorex/Public_ChannelForge/releases/download/";
    private static final String RELEASE_MANIFEST =
            "https://github.com/dethkorex/Public_ChannelForge/releases/latest/download/ChannelForge-release.json";
    private static final Pattern VERSION = Pattern.compile("([0-9]{1,9})\\.([0-9]{1,9})\\.([0-9]{1,9})(?:-(alpha|beta|rc)\\.([0-9]{1,9}))?");
    private static final Pattern SHA256 = Pattern.compile("[a-fA-F0-9]{64}");
    private static final int MAX_APK_BYTES = 100 * 1024 * 1024;

    final String version;
    private final URL apkUrl;
    private final byte[] checksum;

    private FireTvUpdate(String version, URL apkUrl, byte[] checksum) {
        this.version = version;
        this.apkUrl = apkUrl;
        this.checksum = checksum;
    }

    interface DownloadProgress {
        void update(int percent);
    }

    static FireTvUpdate latest(StringBuilder notes) throws Exception {
        JSONObject release = latestRelease();
        String tag = release.getString("tag_name");
        String version = tag.substring(1);
        if (isNewer(BuildConfig.VERSION_NAME, version)) {
            notes.append("You are running a newer build than the latest available release.");
            return null;
        }
        for (String line : release.optString("body").split("\\r?\\n")) {
            String text = line.trim();
            if (text.isEmpty() || text.contains("http://") || text.contains("https://") || text.startsWith("##")) continue;
            if (text.startsWith("- ")) text = "• " + text.substring(2);
            if (notes.length() + text.length() > 1300) break;
            if (notes.length() > 0) notes.append('\n');
            notes.append(text);
        }
        if (!isNewer(version, BuildConfig.VERSION_NAME)) return null;

        JSONArray assets = release.getJSONArray("assets");
        JSONObject apk = findAsset(assets, APK);
        URL apkUrl = officialAsset(apk, tag, APK);
        String digest = apk.optString("digest");
        if (!digest.startsWith("sha256:") || !SHA256.matcher(digest.substring(7)).matches()) {
            URL checksumUrl = officialAsset(findAsset(assets, CHECKSUM), tag, CHECKSUM);
            digest = "sha256:" + publishedChecksum(readText(checksumUrl, 16 * 1024));
        }
        byte[] expected = new byte[32];
        for (int i = 0; i < expected.length; i++) {
            int start = 7 + i * 2;
            expected[i] = (byte) Integer.parseInt(digest.substring(start, start + 2), 16);
        }
        return new FireTvUpdate(version, apkUrl, expected);
    }

    private static JSONObject latestRelease() throws Exception {
        if (BuildConfig.VERSION_NAME.contains("-")) {
            try {
                URL previews = new URL(BuildConfig.GITHUB_RELEASES_API.replace("/latest", "?per_page=10"));
                JSONArray releases = new JSONArray(readText(previews, 1024 * 1024));
                JSONObject newest = null;
                for (int i = 0; i < releases.length(); i++) {
                    JSONObject entry = releases.optJSONObject(i);
                    if (entry != null && validRelease(entry, true) && (newest == null ||
                            isNewer(entry.getString("tag_name").substring(1), newest.getString("tag_name").substring(1)))) {
                        newest = entry;
                    }
                }
                if (newest != null) return newest;
            } catch (Exception unavailable) {
                // The stable manifest still offers the final release when the API is unavailable.
            }
        }
        JSONObject api;
        try {
            api = readRelease(BuildConfig.GITHUB_RELEASES_API);
        } catch (Exception unavailable) {
            return readRelease(RELEASE_MANIFEST);
        }
        String apiVersion = api.getString("tag_name").substring(1);
        if (isNewer(apiVersion, BuildConfig.VERSION_NAME)) return api;
        try {
            JSONObject manifest = readRelease(RELEASE_MANIFEST);
            if (isNewer(manifest.getString("tag_name").substring(1), apiVersion)) return manifest;
        } catch (Exception unavailable) {
            // A verified API response still works when the static manifest is unreachable.
        }
        return api;
    }

    private static boolean validRelease(JSONObject release, boolean preview) {
        String tag = release.optString("tag_name");
        return tag.startsWith("v") && VERSION.matcher(tag.substring(1)).matches()
                && !release.optBoolean("draft") && (preview || (!release.optBoolean("prerelease") && !tag.contains("-")))
                && release.optJSONArray("assets") != null && release.optJSONArray("assets").length() > 0;
    }

    private static JSONObject readRelease(String address) throws Exception {
        JSONObject release = new JSONObject(readText(new URL(address), 1024 * 1024));
        if (!validRelease(release, false)) {
            throw new IOException("GitHub returned an invalid release.");
        }
        return release;
    }

    static boolean isNewer(String candidate, String installed) {
        Matcher next = VERSION.matcher(candidate), current = VERSION.matcher(installed);
        if (!next.matches() || !current.matches()) return false;
        for (int i = 1; i <= 3; i++) {
            int comparison = Integer.compare(Integer.parseInt(next.group(i)), Integer.parseInt(current.group(i)));
            if (comparison != 0) return comparison > 0;
        }
        if (next.group(4) == null) return current.group(4) != null;
        if (current.group(4) == null) return false;
        // alpha, beta and rc sort in their release order.
        int stage = next.group(4).compareTo(current.group(4));
        return stage != 0 ? stage > 0 : Integer.parseInt(next.group(5)) > Integer.parseInt(current.group(5));
    }

    private static JSONObject findAsset(JSONArray assets, String name) throws Exception {
        for (int i = 0; i < assets.length(); i++) {
            JSONObject asset = assets.getJSONObject(i);
            if (name.equals(asset.optString("name"))) return asset;
        }
        throw new IOException("The release is missing " + name + ".");
    }

    private static URL officialAsset(JSONObject asset, String tag, String name) throws IOException {
        String expected = RELEASE_ASSETS + tag + "/" + name;
        if (!expected.equals(asset.optString("browser_download_url"))) {
            throw new IOException("The release contains an unexpected download address.");
        }
        return new URL(expected);
    }

    static String publishedChecksum(String text) throws IOException {
        Pattern entry = Pattern.compile("(?m)^([a-fA-F0-9]{64})[ \\t]+\\*?(?:tv-release/)?"
                + Pattern.quote(APK) + "[ \\t]*\\r?$");
        Matcher match = entry.matcher(text);
        if (!match.find()) throw new IOException("The release has no Fire TV checksum.");
        return match.group(1);
    }

    int stage(Context context, DownloadProgress progress) throws Exception {
        File apk = File.createTempFile("channel-forge-update-", ".apk", context.getCacheDir());
        try {
            download(apk, progress);
            verifyPackage(context, apk);
            return stagePackage(context.getPackageManager().getPackageInstaller(), context.getPackageName(), apk);
        } finally {
            apk.delete();
        }
    }

    private void download(File apk, DownloadProgress progress) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        HttpsURLConnection connection = open(apkUrl);
        try (InputStream input = connection.getInputStream(); OutputStream output = new FileOutputStream(apk)) {
            int size = connection.getContentLength();
            if (size > MAX_APK_BYTES) throw new IOException("The update file is unexpectedly large.");
            byte[] buffer = new byte[64 * 1024];
            int bytes = 0;
            int lastPercent = 0;
            int count;
            while ((count = input.read(buffer)) != -1) {
                bytes += count;
                if (bytes > MAX_APK_BYTES) throw new IOException("The update file is unexpectedly large.");
                if (Thread.currentThread().isInterrupted()) throw new IOException("Update canceled.");
                output.write(buffer, 0, count);
                digest.update(buffer, 0, count);
                int percent = size > 0 ? Math.min(99, (int)(bytes * 100L / size)) : 0;
                if (percent > lastPercent) {
                    progress.update(percent);
                    lastPercent = percent;
                }
            }
            if (bytes == 0 || (size > 0 && bytes != size) || !MessageDigest.isEqual(checksum, digest.digest())) {
                throw new IOException("The update download did not match its checksum. Try again.");
            }
            progress.update(100);
        } finally {
            connection.disconnect();
        }
    }

    @SuppressWarnings("deprecation")
    private void verifyPackage(Context context, File apk) throws Exception {
        PackageManager manager = context.getPackageManager();
        // Some Fire OS versions only collect archive certificates when GET_SIGNATURES is set.
        int flags = PackageManager.GET_SIGNATURES;
        if (Build.VERSION.SDK_INT >= 28) flags |= PackageManager.GET_SIGNING_CERTIFICATES;
        PackageInfo candidate = manager.getPackageArchiveInfo(apk.getAbsolutePath(), flags);
        if (candidate == null || !context.getPackageName().equals(candidate.packageName)
                || !version.equals(candidate.versionName)) {
            throw new IOException("The download is not the expected ChannelForge TV update.");
        }
        long versionCode = Build.VERSION.SDK_INT >= 28 ? candidate.getLongVersionCode() : candidate.versionCode;
        if (versionCode <= BuildConfig.VERSION_CODE) throw new IOException("The APK is not newer than the installed build.");
        PackageInfo installed = manager.getPackageInfo(context.getPackageName(), flags);
        Signature[] expected = signatures(installed);
        Signature[] actual = signatures(candidate);
        if (expected == null || expected.length == 0 || actual == null || actual.length == 0) {
            throw new IOException("Could not read the update signing certificate. Install the official Fire TV APK to update.");
        }
        if (!Arrays.equals(expected, actual)) {
            throw new IOException("This copy has a different signing key. Install the official Fire TV APK to enable updates.");
        }
    }

    @SuppressWarnings("deprecation")
    private static Signature[] signatures(PackageInfo info) {
        if (Build.VERSION.SDK_INT >= 28 && info.signingInfo != null) {
            Signature[] signers = info.signingInfo.getApkContentsSigners();
            if (signers != null && signers.length > 0) return signers;
        }
        return info.signatures;
    }

    private static int stagePackage(PackageInstaller installer, String packageName, File apk) throws IOException {
        PackageInstaller.SessionParams params = new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
        params.setAppPackageName(packageName);
        params.setSize(apk.length());
        if (Build.VERSION.SDK_INT >= 31) params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_REQUIRED);
        int sessionId = installer.createSession(params);
        try (PackageInstaller.Session session = installer.openSession(sessionId);
                InputStream input = new FileInputStream(apk);
                OutputStream output = session.openWrite("base.apk", 0, apk.length())) {
            byte[] buffer = new byte[64 * 1024];
            int count;
            while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
            session.fsync(output);
        } catch (IOException | RuntimeException error) {
            installer.abandonSession(sessionId);
            throw error;
        }
        return sessionId;
    }

    private static String readText(URL url, int limit) throws IOException {
        HttpsURLConnection connection = open(url);
        try (InputStream input = connection.getInputStream(); ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = input.read(buffer)) != -1) {
                if (output.size() + count > limit) throw new IOException("The release information is unexpectedly large.");
                output.write(buffer, 0, count);
            }
            return new String(output.toByteArray(), StandardCharsets.UTF_8);
        } finally {
            connection.disconnect();
        }
    }

    private static HttpsURLConnection open(URL url) throws IOException {
        for (int redirects = 0; redirects <= 5; redirects++) {
            String host = url.getHost();
            if (!"https".equals(url.getProtocol()) || url.getUserInfo() != null
                    || (url.getPort() != -1 && url.getPort() != 443)
                    || !("api.github.com".equals(host) || "github.com".equals(host)
                    || "release-assets.githubusercontent.com".equals(host)
                    || "objects.githubusercontent.com".equals(host))) {
                throw new IOException("The update download redirected away from GitHub.");
            }
            HttpsURLConnection connection = (HttpsURLConnection) url.openConnection();
            try {
                connection.setInstanceFollowRedirects(false);
                connection.setConnectTimeout(10_000);
                connection.setReadTimeout(20_000);
                connection.setRequestProperty("User-Agent", "ChannelForge-TV/" + BuildConfig.VERSION_NAME);
                connection.setRequestProperty("Accept-Encoding", "identity");
                connection.setUseCaches(false);
                connection.setRequestProperty("Cache-Control", "no-cache");
                int status = connection.getResponseCode();
                if (status == 200) return connection;
                if (status != 301 && status != 302 && status != 303 && status != 307 && status != 308) {
                    throw new IOException("GitHub returned HTTP " + status + ".");
                }
                String location = connection.getHeaderField("Location");
                if (location == null) throw new IOException("GitHub returned an invalid redirect.");
                url = new URL(url, location);
            } catch (IOException | RuntimeException error) {
                connection.disconnect();
                throw error;
            }
            connection.disconnect();
        }
        throw new IOException("The update download redirected too many times.");
    }
}
