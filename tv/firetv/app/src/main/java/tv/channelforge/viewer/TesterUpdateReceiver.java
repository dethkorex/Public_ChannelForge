package tv.channelforge.viewer;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;

/** Reopen after a Tester-initiated, system-confirmed replacement. */
public final class TesterUpdateReceiver extends BroadcastReceiver {
    static final String PREFS = "channel_forge_tv";
    static final String REOPEN_AFTER_UPDATE = "reopen_after_tester_update";

    @Override
    @androidx.annotation.OptIn(markerClass = androidx.media3.common.util.UnstableApi.class)
    public void onReceive(Context context, Intent intent) {
        if (!BuildConfig.TESTER_BUILD || !Intent.ACTION_MY_PACKAGE_REPLACED.equals(intent.getAction())) return;
        SharedPreferences preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        int expected = preferences.getInt(REOPEN_AFTER_UPDATE, 0);
        preferences.edit().remove(REOPEN_AFTER_UPDATE).apply();
        if (expected != BuildConfig.VERSION_CODE) return;
        Intent launch = new Intent(context, MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK);
        try {
            context.startActivity(launch);
        } catch (SecurityException ignored) {
            // The installer can still offer Open on versions that restrict background launches.
        }
    }
}
