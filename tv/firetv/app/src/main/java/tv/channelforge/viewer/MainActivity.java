package tv.channelforge.viewer;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.PendingIntent;
import android.content.ActivityNotFoundException;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageInstaller;
import android.graphics.Color;
import android.graphics.BitmapFactory;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.SystemClock;
import android.provider.Settings;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.widget.*;
import androidx.media3.common.AudioAttributes;
import androidx.media3.common.MediaItem;
import androidx.media3.common.MimeTypes;
import androidx.media3.common.PlaybackException;
import androidx.media3.common.Player;
import androidx.media3.common.util.UnstableApi;
import androidx.media3.datasource.DefaultHttpDataSource;
import androidx.media3.datasource.HttpDataSource;
import androidx.media3.exoplayer.DefaultLoadControl;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory;
import androidx.media3.exoplayer.upstream.DefaultBandwidthMeter;
import androidx.media3.ui.PlayerView;
import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

@UnstableApi
public class MainActivity extends Activity {
    private final List<Creator> creators = new ArrayList<>();
    private final List<Channel> channels = new ArrayList<>();
    private final List<VaultItem> vaultItems = new ArrayList<>();
    private PlayerView video;
    private ExoPlayer player;
    private boolean renderedFrame;
    private LinearLayout guide, creatorRail, channelRail;
    private TextView status, banner, updateButton;
    private LinearLayout bufferPanel, bannerPanel;
    private ProgressBar bufferSpinner;
    private TextView bufferLabel;
    private String streamTitle = "";
    private int channelIndex = 0;
    private boolean guideVisible = true;
    private Channel tunedChannel;
    private int liveRetries;
    private long tunedProgramEnd;
    private boolean hasUpcomingProgram;
    private final Runnable upcomingProgram = () -> {
        if (tunedChannel != null && !guideVisible && player.isPlaying()) showChannelBanner(tunedChannel);
    };
    private final Runnable liveProgramBoundary = this::retuneAtBoundary;
    private boolean playbackWaiting, playbackFailed;
    private String activeMovieUrl = "";
    private final Runnable playbackDeadline = () -> {
        playbackWaiting = false;
        if (!guideVisible) {
            video.removeCallbacks(this.continueLive);
            player.stop();
            failPlayback();
        }
    };
    private final Runnable continueLive = () -> {
        if (tunedChannel != null && !guideVisible) {
            trackLiveProgram();
            if (!hasUpcomingProgram) refreshOnAirGuide(tunedChannel);
            showBuffering("Connecting to the studio…", true);
            waitForPlayback();
            startStream(tunedChannel.url, true);
        }
    };
    private boolean directoryLoading = false;
    private long directoryMissStarted;
    private final ExecutorService posterLoader = Executors.newFixedThreadPool(3);
    private final ExecutorService studioLoader = Executors.newFixedThreadPool(4);
    private final ExecutorService updateLoader = Executors.newSingleThreadExecutor();
    private static final String UPDATE_RESULT = BuildConfig.APPLICATION_ID + ".UPDATE_RESULT";
    private static final String UPDATE_PERMISSION = BuildConfig.APPLICATION_ID + ".permission.UPDATE_RESULT";
    private static final int INSTALL_SETTINGS_REQUEST = 21;
    private FireTvUpdate pendingUpdate;
    private boolean updateBusy, resumed;
    private int updateSession = -1;
    private boolean sessionCommitted, installerOpened;
    private Intent installConfirmation;
    private final BroadcastReceiver updateResult = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            if (updateSession < 0 || intent.getIntExtra(PackageInstaller.EXTRA_SESSION_ID, -1) != updateSession) return;
            int status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
            if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
                installConfirmation = intent.getParcelableExtra(Intent.EXTRA_INTENT);
                if (installConfirmation == null) {
                    abandonUpdate(updateSession);
                    finishUpdate("Fire TV did not provide an installation prompt. Try again.");
                } else {
                    openInstallConfirmation();
                }
            } else if (status == PackageInstaller.STATUS_SUCCESS) {
                pendingUpdate = null;
                finishUpdate("Update installed.");
            } else if (status == PackageInstaller.STATUS_FAILURE_ABORTED) {
                finishUpdate("Installation canceled. Select Updates to try again.");
            } else if (status == PackageInstaller.STATUS_FAILURE_BLOCKED && !canInstallUpdates()) {
                finishUpdate("Fire TV blocked the update. Allow ChannelForge TV to install unknown apps and try again.");
            } else {
                finishUpdate("Fire TV could not install the update. Select Updates to try again.");
            }
        }
    };
    private LinearLayout liveRail;
    private LinearLayout movieRail;
    private TextView screenSwitcher;
    private boolean cinemaScreen;
    private Creator currentStudio;
    private int studioRequest = 0;
    private int movieRequest = 0;
    private boolean moviePageLoading, movieShelfLoaded;
    private int nextMovieOffset = -1;
    private LinearLayout movieShelf;
    private TextView moreMovies;
    private String selectedPlaylist = "";
    private static final String PREFS = "channel_forge_tv";
    private static final String PAIRED_CREATOR = "paired_creator_url";
    private static final long DIRECTORY_REFRESH_MS = 60_000L;
    private static final long DIRECTORY_RETRY_MS = 10_000L;
    private static final long DIRECTORY_GRACE_MS = 30_000L;
    private static final int MOVIE_PAGE_SIZE = 12;
    private static final long METADATA_DEADLINE_MS = 20_000L;
    private static final int MAX_METADATA_BYTES = 24 * 1024 * 1024;
    private final Runnable directoryRetry = this::loadDirectory;
    private final Runnable directoryRefresh = new Runnable() {
        @Override public void run() {
            if (guideVisible) loadDirectory();
            if (guide != null) guide.postDelayed(this, DIRECTORY_REFRESH_MS);
        }
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().getDecorView().setSystemUiVisibility(5894);
        buildUi();
        registerUpdateResult();
        loadDirectory();
        guide.postDelayed(directoryRefresh, DIRECTORY_REFRESH_MS);
        checkUpdates(false);
    }

    @SuppressLint("UnspecifiedRegisterReceiverFlag") // Earlier Fire OS versions use the signature permission.
    private void registerUpdateResult() {
        IntentFilter updateFilter = new IntentFilter(UPDATE_RESULT);
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(updateResult, updateFilter, UPDATE_PERMISSION, null, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(updateResult, updateFilter, UPDATE_PERMISSION, null);
        }
    }

    private TextView text(String value, int size, boolean bold) {
        TextView view = new TextView(this); view.setText(value); view.setTextColor(Color.WHITE); view.setTextSize(size);
        view.setPadding(dp(18), dp(10), dp(18), dp(10)); if (bold) view.setTypeface(Typeface.DEFAULT_BOLD); return view;
    }

    private int dp(int value){return Math.round(value*getResources().getDisplayMetrics().density);}
    private int color(String value){return Color.parseColor(value);}
    private GradientDrawable surface(int[] colors,int strokeColor,int strokeWidth,int radius){GradientDrawable value=new GradientDrawable(GradientDrawable.Orientation.TL_BR,colors);value.setCornerRadius(dp(radius));if(strokeWidth>0)value.setStroke(dp(strokeWidth),strokeColor);return value;}
    private LinearLayout panel(){LinearLayout value=new LinearLayout(this);value.setOrientation(LinearLayout.VERTICAL);value.setPadding(dp(16),dp(15),dp(16),dp(15));value.setBackground(surface(new int[]{color("#101938"),color("#090B20")},color("#174F75"),1,14));return value;}
    private TextView sectionTitle(String value,String accent){TextView title=text(value,14,true);title.setTextColor(color(accent));title.setLetterSpacing(.10f);return title;}
    private void addGap(LinearLayout parent,int height){Space gap=new Space(this);parent.addView(gap,new LinearLayout.LayoutParams(1,dp(height)));}

    private void buildUi() {
        FrameLayout root = new FrameLayout(this); root.setBackground(surface(new int[]{color("#050616"),color("#25073D"),color("#070312")},0,0,0));
        video = new PlayerView(this);
        DefaultHttpDataSource.Factory http = new DefaultHttpDataSource.Factory()
                .setUserAgent("ChannelForge-TV/" + BuildConfig.VERSION_NAME)
                .setConnectTimeoutMs(6000).setReadTimeoutMs(10000);
        player = new ExoPlayer.Builder(this)
                .setMediaSourceFactory(new DefaultMediaSourceFactory(http))
                .setLoadControl(new DefaultLoadControl.Builder().setBufferDurationsMs(8000, 25000, 900, 2000).build())
                .setBandwidthMeter(new DefaultBandwidthMeter.Builder(this).setInitialBitrateEstimate(320_000L).build())
                .setAudioAttributes(AudioAttributes.DEFAULT, true)
                .build();
        player.setTrackSelectionParameters(player.getTrackSelectionParameters().buildUpon()
                .setPreferredAudioLanguage("en").build());
        video.setPlayer(player);
        video.setUseController(false);
        video.setKeepContentOnPlayerReset(true);
        video.setFocusable(false);
        root.addView(video, new FrameLayout.LayoutParams(-1, -1));
        guide = new LinearLayout(this); guide.setOrientation(LinearLayout.VERTICAL); guide.setPadding(dp(26),dp(16),dp(26),dp(15)); guide.setBackgroundColor(Color.argb(238, 4, 5, 20));
        LinearLayout header=new LinearLayout(this);header.setGravity(Gravity.CENTER_VERTICAL);header.setPadding(0,0,0,dp(10));
        ImageView logo=new ImageView(this);logo.setImageResource(R.mipmap.channel_forge_tv_icon);logo.setScaleType(ImageView.ScaleType.FIT_CENTER);header.addView(logo,new LinearLayout.LayoutParams(dp(68),dp(68)));
        LinearLayout brandBox=new LinearLayout(this);brandBox.setOrientation(LinearLayout.VERTICAL);TextView brand=text("CHANNELFORGE TV",25,true);brand.setTextColor(color("#FFF229"));brand.setLetterSpacing(.16f);brandBox.addView(brand);TextView subtitle=text("THE COMMUNITY DIAL",11,true);subtitle.setTextColor(color("#55EFFF"));subtitle.setLetterSpacing(.28f);brandBox.addView(subtitle);header.addView(brandBox,new LinearLayout.LayoutParams(0,-2,1));
        screenSwitcher=focusButton("REWIND CINEMA");
        screenSwitcher.setOnClickListener(v -> {
            cinemaScreen = !cinemaScreen;
            syncStudioScreen();
            if (cinemaScreen && currentStudio != null && !movieShelfLoaded && !moviePageLoading)
                loadMoviePage(currentStudio, 0);
        });
        header.addView(screenSwitcher);
        updateButton=focusButton("UPDATES  •  v"+BuildConfig.VERSION_NAME);updateButton.setOnClickListener(v->selectUpdate());LinearLayout.LayoutParams updateLayout=new LinearLayout.LayoutParams(-2,-2);updateLayout.setMargins(dp(10),0,0,0);header.addView(updateButton,updateLayout);guide.addView(header);
        screenSwitcher.setId(View.generateViewId());
        updateButton.setId(View.generateViewId());
        screenSwitcher.setNextFocusRightId(updateButton.getId());
        updateButton.setNextFocusLeftId(screenSwitcher.getId());
        View line=new View(this);line.setBackgroundColor(color("#16DFF5"));guide.addView(line,new LinearLayout.LayoutParams(-1,dp(2)));
        status = text("●  CONNECTING TO THE PUBLIC CHANNEL DIRECTORY…", 12, true); status.setTextColor(color("#77F6A3"));status.setBackground(surface(new int[]{color("#111A3B"),color("#0A0B20")},color("#225E8B"),1,11));LinearLayout.LayoutParams statusLayout=new LinearLayout.LayoutParams(-1,-2);statusLayout.setMargins(0,dp(12),0,dp(12));guide.addView(status,statusLayout);
        LinearLayout columns = new LinearLayout(this); columns.setOrientation(LinearLayout.HORIZONTAL); guide.addView(columns, new LinearLayout.LayoutParams(-1, 0, 1));
        LinearLayout creatorsPanel=panel();creatorRail=new LinearLayout(this);creatorRail.setOrientation(LinearLayout.VERTICAL);ScrollView creatorScroll=new ScrollView(this);creatorScroll.setVerticalScrollBarEnabled(false);creatorScroll.addView(creatorRail);creatorsPanel.addView(creatorScroll,new LinearLayout.LayoutParams(-1,0,1));LinearLayout.LayoutParams creatorsLayout=new LinearLayout.LayoutParams(0,-1,.29f);creatorsLayout.setMargins(0,0,dp(12),0);columns.addView(creatorsPanel,creatorsLayout);
        LinearLayout channelsPanel=panel();channelRail=new LinearLayout(this);channelRail.setOrientation(LinearLayout.VERTICAL);ScrollView channelScroll=new ScrollView(this);channelScroll.setVerticalScrollBarEnabled(false);channelScroll.addView(channelRail);channelsPanel.addView(channelScroll,new LinearLayout.LayoutParams(-1,0,1));columns.addView(channelsPanel,new LinearLayout.LayoutParams(0,-1,.71f));
        creatorRail.addView(sectionTitle("COMMUNITY STUDIOS","#5DEEFF"));creatorRail.addView(text("Searching worldwide…",14,false));
        channelRail.addView(sectionTitle("WELCOME TO CHANNELFORGE TV","#FF48C8"));TextView welcome=text("Live creator channels and Rewind Cinema in one standalone Fire TV app.",24,true);welcome.setTextColor(color("#FFF229"));channelRail.addView(welcome);channelRail.addView(text("No Plex setup is needed on this Fire TV. Public studios appear here automatically when they are online.",15,false));
        TextView help = text("↑↓  SURF CHANNELS     •     OK  WATCH / GUIDE     •     BACK  RETURN TO GUIDE", 10, true); help.setTextColor(color("#7184A9")); guide.addView(help);
        root.addView(guide, new FrameLayout.LayoutParams(-1,-1));
        bufferPanel = panel();
        bufferPanel.setGravity(Gravity.CENTER);
        bufferSpinner = new ProgressBar(this);
        bufferPanel.addView(bufferSpinner, new LinearLayout.LayoutParams(dp(38), dp(38)));
        bufferLabel = text("", 18, true);
        bufferLabel.setGravity(Gravity.CENTER);
        bufferPanel.addView(bufferLabel, new LinearLayout.LayoutParams(-1, -2));
        bufferPanel.setVisibility(View.GONE);
        root.addView(bufferPanel, new FrameLayout.LayoutParams(dp(360), -2, Gravity.CENTER));
        bannerPanel = new LinearLayout(this);
        bannerPanel.setGravity(Gravity.CENTER_VERTICAL);
        bannerPanel.setPadding(dp(12), dp(8), dp(12), dp(8));
        bannerPanel.setBackground(surface(new int[]{color("#111938"),color("#290A3E")},color("#12DFF9"),2,12));
        banner = text("", 18, true);
        bannerPanel.setVisibility(View.GONE);
        FrameLayout.LayoutParams bp = new FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM);
        bp.setMargins(dp(35), 0, dp(35), dp(35));
        root.addView(bannerPanel, bp);
        setContentView(root);
        screenSwitcher.requestFocus();
        player.addListener(new Player.Listener() {
            @Override public void onPlaybackStateChanged(int state) {
                if (guideVisible) return;
                if (state == Player.STATE_BUFFERING) {
                    showBuffering("Buffering video…", true);
                    waitForPlayback();
                } else if (state == Player.STATE_ENDED) {
                    clearPlaybackDeadline();
                    if (tunedChannel != null) {
                        retuneAtBoundary();
                    } else returnToGuide();
                }
            }

            @Override public void onRenderedFirstFrame() {
                if (guideVisible) return;
                renderedFrame = true;
                hidePlaybackBuffer();
                if (tunedChannel != null) showChannelBanner(tunedChannel);
            }

            @Override public void onIsPlayingChanged(boolean playing) {
                if (playing && renderedFrame && !guideVisible) hidePlaybackBuffer();
            }

            @Override public void onPlayerError(PlaybackException error) {
                if (guideVisible) return;
                clearPlaybackDeadline();
                Throwable cause = error.getCause();
                boolean unavailable = cause instanceof HttpDataSource.InvalidResponseCodeException
                        && (((HttpDataSource.InvalidResponseCodeException) cause).responseCode == 404
                        || ((HttpDataSource.InvalidResponseCodeException) cause).responseCode == 410);
                if (tunedChannel != null && unavailable && !renderedFrame) {
                    video.removeCallbacks(liveProgramBoundary);
                    playbackFailed = true;
                    showBuffering("This channel is offline. Press OK to retry or Back for the guide.", false);
                } else if (tunedChannel != null && liveRetries < 2) {
                    showBuffering("Reconnecting to the studio…", true);
                    scheduleLive(1200L * ++liveRetries);
                } else failPlayback();
            }
        });
    }

    private String get(String address) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + METADATA_DEADLINE_MS;
        HttpURLConnection connection = (HttpURLConnection) new URL(address).openConnection();
        connection.setConnectTimeout(6000);
        connection.setReadTimeout(8000);
        connection.setRequestProperty("Accept", "application/json, application/x-mpegURL, */*");
        connection.setRequestProperty("User-Agent", "ChannelForgeTV/" + BuildConfig.VERSION_NAME);
        try {
            int code = connection.getResponseCode();
            if (code < 200 || code >= 300) throw new IOException("Creator returned HTTP " + code);
            try (InputStream input = connection.getInputStream()) {
                ByteArrayOutputStream payload = new ByteArrayOutputStream();
                byte[] buffer = new byte[8192];
                int count;
                while ((count = input.read(buffer)) != -1) {
                    if (SystemClock.elapsedRealtime() > deadline) throw new IOException("Studio response timed out.");
                    if (payload.size() + count > MAX_METADATA_BYTES) throw new IOException("Studio response is too large.");
                    payload.write(buffer, 0, count);
                }
                return new String(payload.toByteArray(), StandardCharsets.UTF_8);
            }
        } finally {
            connection.disconnect();
        }
    }

    private void loadDirectory() {
        if(directoryLoading)return;
        directoryLoading=true;
        if (creators.isEmpty()) status.setText("Scanning the public ChannelForge directory…");
        new Thread(() -> {
            List<Creator> discovered=new ArrayList<>();
            Exception last=null;
            for(String address:new String[]{BuildConfig.DIRECTORY_URL,BuildConfig.DIRECTORY_FALLBACK_URL}){
                try{
                    JSONArray values=new JSONObject(get(address)).getJSONArray("creators");
                    for(int i=0;i<values.length();i++){
                        JSONObject o = values.optJSONObject(i);
                        if (o == null) continue;
                        String playlist = o.optString("playlist_url");
                        if (!playlist.isEmpty() && !hasCreator(discovered, playlist)) {
                            discovered.add(new Creator(o.optString("name", "Creator"), o.optString("description"),
                                    playlist, o.optString("vault_url"), o.optString("status_url")));
                        }
                    }
                    if(!discovered.isEmpty())break;
                }catch(Exception error){last=error;}
            }
            String paired = getSharedPreferences(PREFS, MODE_PRIVATE).getString(PAIRED_CREATOR, "");
            if (!paired.isEmpty() && !hasCreator(discovered, paired + "/exports/channels.m3u")) {
                try { discovered.add(readPairedCreator(paired)); } catch (Exception ignored) {}
            }
            Exception failure=last;
            runOnUiThread(() -> applyDirectory(discovered, failure));
        }).start();
    }

    private void applyDirectory(List<Creator> discovered, Exception failure) {
        directoryLoading = false;
        if (isFinishing() || isDestroyed()) return;
        guide.removeCallbacks(directoryRetry);
        if (discovered.isEmpty()) {
            if (!creators.isEmpty()) {
                long now = SystemClock.elapsedRealtime();
                if (directoryMissStarted == 0) directoryMissStarted = now;
                if (now - directoryMissStarted < DIRECTORY_GRACE_MS) {
                    status.setText("Checking worldwide studios… Showing the last scan.");
                    if (guideVisible) guide.postDelayed(directoryRetry, DIRECTORY_RETRY_MS);
                    return;
                }
            }
            creators.clear();
            renderDirectoryRecovery(failure);
            return;
        }
        directoryMissStarted = 0;
        boolean changed = !sameCreators(discovered);
        creators.clear();
        creators.addAll(discovered);
        if (changed || liveRail == null) {
            renderCreators();
        } else {
            for (Creator creator : creators) {
                if (creator.playlist.equals(selectedPlaylist)) {
                    loadCreator(creator);
                    break;
                }
            }
        }
    }

    private boolean sameCreators(List<Creator> discovered) {
        if (creators.size() != discovered.size()) return false;
        for (int i = 0; i < creators.size(); i++) {
            Creator old = creators.get(i), current = discovered.get(i);
            if (!old.playlist.equals(current.playlist) || !old.name.equals(current.name)
                    || !old.description.equals(current.description) || !old.vault.equals(current.vault)
                    || !old.channelStatus.equals(current.channelStatus)) return false;
        }
        return true;
    }

    private boolean hasCreator(List<Creator> values,String playlist){for(Creator creator:values)if(creator.playlist.equals(playlist))return true;return false;}

    private Creator readPairedCreator(String raw) throws Exception {
        String base=raw.trim().replaceAll("/+$","");
        if(base.endsWith("/api/public/channels"))base=base.substring(0,base.length()-"/api/public/channels".length());
        if(!base.startsWith("https://"))throw new IOException("Pairing URLs must use HTTPS");
        JSONObject state=new JSONObject(get(base+"/api/public/channels?guide=brief"));
        if(!state.has("channels"))throw new IOException("That URL is not a ChannelForge Creator");
        String host=Uri.parse(base).getHost();
        return new Creator(host==null?"Paired Creator":host,"Paired directly with this Fire TV",base+"/exports/channels.m3u",base+"/api/public/vault",base+"/api/public/channels");
    }

    private void renderDirectoryRecovery(Exception error){
        creatorRail.removeAllViews();channelRail.removeAllViews();
        liveRail = null;
        movieRail = null;
        currentStudio = null;
        ++studioRequest;
        ++movieRequest;
        status.setText(error == null ? "NO PUBLIC STUDIOS ONLINE" : "PUBLIC DIRECTORY UNAVAILABLE  •  SELECT SCAN TO RETRY");
        creatorRail.addView(sectionTitle("DISCOVERY","#5DEEFF"));
        TextView retry=focusButton("↻  SCAN PUBLIC DIRECTORY");retry.setOnClickListener(v->loadDirectory());creatorRail.addView(retry,new LinearLayout.LayoutParams(-1,-2));addGap(creatorRail,10);
        TextView pair=focusButton("＋  OPTIONAL DIRECT PAIR");pair.setOnClickListener(v->showPairingDialog());creatorRail.addView(pair,new LinearLayout.LayoutParams(-1,-2));
        creatorRail.addView(text("Pairing is optional. It connects one published studio directly while the global directory is unavailable.",12,false));
        channelRail.addView(sectionTitle("CHANNELFORGE TV","#FF48C8"));
        TextView hero=text("TURN THE DIAL.\nFIND SOMETHING UNFORGETTABLE.",27,true);hero.setTextColor(color("#FFF229"));hero.setBackground(surface(new int[]{color("#1A1450"),color("#32103F")},color("#12DFF9"),1,14));channelRail.addView(hero,new LinearLayout.LayoutParams(-1,-2));addGap(channelRail,12);
        TextView detail=text(error==null?"No public creator studios are broadcasting right now.":"Both worldwide directory sources are unavailable. Your Fire TV app can retry without needing Creator installed on this device.",14,false);detail.setTextColor(color("#C6D1E7"));channelRail.addView(detail);
        channelRail.addView(sectionTitle("WHAT APPEARS HERE","#5DEEFF"));channelRail.addView(text("• Live channels and the full program guide\n• Poster artwork and upcoming show details\n• Rewind Cinema movie shelves from online studios",14,false));
        retry.requestFocus();
    }

    private void showPairingDialog(){
        EditText input=new EditText(this);input.setSingleLine(true);input.setHint("https://your-studio.trycloudflare.com");input.setText(getSharedPreferences(PREFS,MODE_PRIVATE).getString(PAIRED_CREATOR,""));input.setSelectAllOnFocus(true);input.setPadding(30,18,30,18);
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("Pair ChannelForge Creator").setMessage("In Creator, open System → Publish worldwide and copy its HTTPS pairing URL.").setView(input).setNegativeButton("CANCEL",null).setNeutralButton("FORGET",(d,w)->{getSharedPreferences(PREFS,MODE_PRIVATE).edit().remove(PAIRED_CREATOR).apply();loadDirectory();}).setPositiveButton("PAIR",null).create();
        dialog.setOnShowListener(ignored->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{String address=input.getText().toString().trim();if(address.isEmpty()){input.setError("Enter the Creator HTTPS URL");return;}dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(false);status.setText("Pairing with Creator…");new Thread(()->{try{Creator creator=readPairedCreator(address);getSharedPreferences(PREFS,MODE_PRIVATE).edit().putString(PAIRED_CREATOR,address.replaceAll("/+$","")).apply();runOnUiThread(()->{dialog.dismiss();creators.clear();creators.add(creator);renderCreators();});}catch(Exception error){runOnUiThread(()->{dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(true);input.setError("Could not reach this Creator. Check that Creator is open and the URL begins with https://");});}}).start();}));
        dialog.show();
    }

    private TextView focusButton(String label) { TextView button=text(label,14,true);button.setTextColor(color("#DFFBFF"));button.setGravity(Gravity.CENTER_VERTICAL);button.setMinHeight(dp(48));button.setFocusable(true);button.setBackground(surface(new int[]{color("#151A3D"),color("#101127")},color("#1BDFF5"),1,9));button.setOnFocusChangeListener((v, focused)->{v.setBackground(surface(focused?new int[]{color("#6A145C"),color("#231454")}:new int[]{color("#151A3D"),color("#101127")},focused?color("#FF42C5"):color("#1BDFF5"),focused?2:1,9));v.setScaleX(focused?1.025f:1f);v.setScaleY(focused?1.025f:1f);v.setTranslationZ(focused?dp(8):0);});return button;}

    private void renderCreators() {
        creatorRail.removeAllViews();
        status.setText("●  " + creators.size() + " PUBLIC CREATOR STUDIOS ONLINE");
        creatorRail.addView(sectionTitle("COMMUNITY STUDIOS", "#5DEEFF"));
        Creator selected = creators.get(0);
        for (Creator creator : creators) {
            TextView button = focusButton(creator.name + "\n" + creator.description);
            button.setOnClickListener(view -> loadCreator(creator));
            LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(-1, -2);
            layout.setMargins(0, 0, 0, dp(9));
            creatorRail.addView(button, layout);
            if (creator.playlist.equals(selectedPlaylist)) selected = creator;
        }
        loadCreator(selected);
    }

    private void loadCreator(Creator creator) {
        currentStudio = creator;
        boolean changed = !creator.playlist.equals(selectedPlaylist) || liveRail == null || movieRail == null;
        selectedPlaylist = creator.playlist;
        if (changed) {
            ++movieRequest;
            moviePageLoading = movieShelfLoaded = false;
            nextMovieOffset = -1;
            movieShelf = null;
            moreMovies = null;
            channels.clear();
            vaultItems.clear();
            channelIndex = 0;
            channelRail.removeAllViews();
            liveRail = new LinearLayout(this);
            liveRail.setOrientation(LinearLayout.VERTICAL);
            movieRail = new LinearLayout(this);
            movieRail.setOrientation(LinearLayout.VERTICAL);
            channelRail.addView(liveRail);
            channelRail.addView(movieRail);
            liveRail.addView(text("Finding live channels…", 14, false));
            movieRail.addView(text("Loading Rewind Cinema…", 14, false));
        }
        syncStudioScreen();
        int request = ++studioRequest;
        studioLoader.execute(() -> {
            List<Channel> loaded = null;
            try {
                if (!creator.channelStatus.isEmpty()) {
                    String brief = Uri.parse(creator.channelStatus).buildUpon().appendQueryParameter("guide", "brief").build().toString();
                    loaded = parseOnlineChannels(get(brief), brief);
                }
            } catch (Exception unavailable) {
                // The playlist is independent of the guide and still allows tuning.
            }
            if (loaded == null) {
                try {
                    String playlist = get(creator.playlist);
                    if (playlist.startsWith("#EXTM3U")) loaded = parseM3u(playlist);
                } catch (Exception ignored) {}
            }
            List<Channel> availableChannels = loaded;
            runOnUiThread(() -> {
                if (isFinishing() || isDestroyed() || request != studioRequest) return;
                if (availableChannels == null) {
                    if (channels.isEmpty()) {
                        liveRail.removeAllViews();
                        liveRail.addView(sectionTitle("LIVE CHANNELS", "#5DEEFF"));
                        TextView retry = focusButton("RETRY LIVE CHANNELS");
                        retry.setOnClickListener(v -> loadCreator(creator));
                        liveRail.addView(retry);
                    }
                    return;
                }
                channels.clear();
                channels.addAll(availableChannels);
                channelIndex = Math.min(channelIndex, Math.max(0, channels.size() - 1));
                renderChannels(creator.name);
            });
        });
        if (cinemaScreen && !movieShelfLoaded && !moviePageLoading) loadMoviePage(creator, 0);
    }

    private void syncStudioScreen() {
        screenSwitcher.setText(cinemaScreen ? "LIVE TV GUIDE" : "REWIND CINEMA");
        if (liveRail != null) liveRail.setVisibility(cinemaScreen ? View.GONE : View.VISIBLE);
        if (movieRail != null) movieRail.setVisibility(cinemaScreen ? View.VISIBLE : View.GONE);
    }

    private void loadMoviePage(Creator creator, int offset) {
        if (moviePageLoading) return;
        moviePageLoading = true;
        int movieLoad = ++movieRequest;
        if (moreMovies != null) moreMovies.setText("LOADING MOVIES…");
        studioLoader.execute(() -> {
            try {
                JSONObject page = new JSONObject();
                if (!creator.vault.isEmpty()) {
                    String address = Uri.parse(creator.vault).buildUpon()
                            .appendQueryParameter("limit", String.valueOf(MOVIE_PAGE_SIZE))
                            .appendQueryParameter("offset", String.valueOf(offset)).build().toString();
                    page = new JSONObject(get(address));
                }
                List<VaultItem> movies = creator.vault.isEmpty() ? Collections.emptyList() : parseVault(page);
                int followingOffset = page.optInt("next_offset", -1);
                runOnUiThread(() -> {
                    if (isFinishing() || isDestroyed() || movieLoad != movieRequest) return;
                    moviePageLoading = false;
                    movieShelfLoaded = true;
                    nextMovieOffset = followingOffset > offset ? followingOffset : -1;
                    if (offset == 0) {
                        vaultItems.clear();
                        vaultItems.addAll(movies);
                        renderMovies(creator);
                    } else {
                        Set<String> known = new HashSet<>();
                        for (VaultItem item : vaultItems) known.add(item.url);
                        for (VaultItem item : movies) if (known.add(item.url)) vaultItems.add(item);
                        updateStudioStatus(creator.name);
                        growMovieShelf(creator);
                    }
                });
            } catch (Exception unavailable) {
                runOnUiThread(() -> {
                    if (isFinishing() || isDestroyed() || movieLoad != movieRequest) return;
                    moviePageLoading = false;
                    if (moreMovies != null) {
                        moreMovies.setText("RETRY MORE MOVIES");
                    } else {
                        status.setText("●  " + creator.name + "  •  " + channels.size()
                                + " LIVE CHANNELS  •  MOVIE CATALOG TIMED OUT");
                        movieRail.removeAllViews();
                        movieRail.addView(sectionTitle("REWIND CINEMA", "#FF48C8"));
                        TextView retry = focusButton("RETRY MOVIE CATALOG");
                        retry.setOnClickListener(v -> loadMoviePage(creator, 0));
                        movieRail.addView(retry);
                    }
                });
            }
        });
    }

    private List<Channel> parseOnlineChannels(String payload, String guideUrl) throws JSONException {
        List<Channel> result = new ArrayList<>();
        JSONArray entries = new JSONObject(payload).getJSONArray("channels");
        for (int i = 0; i < entries.length(); i++) {
            JSONObject entry = entries.getJSONObject(i);
            if (entry.optBoolean("online")) {
                Channel channel = new Channel(entry.getString("name"), entry.getString("stream_url"));
                channel.number = entry.optString("number");
                channel.logo = entry.optString("logo_url");
                channel.programs = entry.optJSONArray("programs");
                channel.guideUrl = guideUrl;
                result.add(channel);
            }
        }
        return result;
    }

    private List<VaultItem> parseVault(JSONObject value) throws JSONException {
        List<VaultItem> result = new ArrayList<>();
        JSONArray items = value.getJSONArray("items");
        for (int i = 0; i < items.length(); i++) {
            JSONObject item = items.getJSONObject(i);
            if (!"movie".equals(item.optString("type"))) continue;
            result.add(new VaultItem(item.optString("title", "Untitled"), item.optString("year"),
                    item.optString("summary"), item.optString("thumbnail_url", item.optString("poster_url")), item.getString("stream_url")));
        }
        return result;
    }

    private List<Channel> parseM3u(String m3u){List<Channel> result=new ArrayList<>();String pending="";for(String raw:m3u.split("\\r?\\n")){String line=raw.trim();if(line.startsWith("#EXTINF:")){int comma=line.lastIndexOf(',');pending=comma>=0?line.substring(comma+1).trim():"Channel";}else if(!line.isEmpty()&&!line.startsWith("#")&&!pending.isEmpty()){result.add(new Channel(pending,line));pending="";}}return result;}

    private void renderChannels(String creator) {
        liveRail.removeAllViews();
        updateStudioStatus(creator);
        liveRail.addView(sectionTitle("LIVE CHANNELS", "#5DEEFF"));
        if (channels.isEmpty()) liveRail.addView(text("No live channels are available from this studio.", 14, false));
        for (int i = 0; i < channels.size(); i++) {
            final int index = i;
            Channel channel = channels.get(i);
            TextView button = focusButton("CH " + displayNumber(channel, i) + "   " + channel.name
                    + "\n● LIVE NOW  •  PRESS OK TO WATCH");
            button.setOnClickListener(v -> play(index));
            LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(-1, -2);
            layout.setMargins(0, 0, 0, dp(8));
            liveRail.addView(button, layout);
        }
    }

    private void updateStudioStatus(String creator) {
        status.setText("●  " + creator.toUpperCase(Locale.US) + "  •  " + channels.size()
                + " LIVE CHANNELS  •  " + (movieShelfLoaded
                ? vaultItems.size() + (nextMovieOffset >= 0 ? "+" : "") + " MOVIES" : "CHECKING MOVIES"));
    }

    private void renderMovies(Creator creator) {
        movieRail.removeAllViews();
        updateStudioStatus(creator.name);
        FrameLayout hero = new FrameLayout(this);
        ImageView artwork = new ImageView(this);
        artwork.setImageResource(R.drawable.cinema_hero);
        artwork.setScaleType(ImageView.ScaleType.CENTER_CROP);
        hero.addView(artwork, new FrameLayout.LayoutParams(-1, -1));
        TextView title = sectionTitle("REWIND CINEMA", "#FF48C8");
        title.setTextSize(24);
        title.setGravity(Gravity.CENTER);
        hero.addView(title, new FrameLayout.LayoutParams(-1, -1));
        movieRail.addView(hero, new LinearLayout.LayoutParams(-1, dp(102)));
        if (vaultItems.isEmpty()) {
            movieRail.addView(text("No movies available from this studio.", 14, false));
            return;
        }
        LinearLayout shelf = new LinearLayout(this);
        movieShelf = shelf;
        shelf.setPadding(dp(8), dp(10), dp(8), dp(20));
        growMovieShelf(creator);
        HorizontalScrollView scroll = new HorizontalScrollView(this) {
            @Override protected void onScrollChanged(int left, int top, int oldLeft, int oldTop) {
                super.onScrollChanged(left, top, oldLeft, oldTop);
                if (shelf == movieShelf && moreMovies == null && shelf.getChildCount() < vaultItems.size()
                        && left + getWidth() + dp(640) >= shelf.getWidth()) growMovieShelf(creator);
            }
        };
        scroll.setHorizontalScrollBarEnabled(false);
        scroll.addView(shelf);
        movieRail.addView(scroll, new LinearLayout.LayoutParams(-1, -2));
    }

    private void growMovieShelf(Creator creator) {
        boolean moveFocus = moreMovies != null && moreMovies.isFocused();
        if (moreMovies != null) {
            movieShelf.removeView(moreMovies);
            moreMovies = null;
        }
        int start = movieShelf.getChildCount();
        int end = Math.min(start + 18, vaultItems.size());
        for (int i = start; i < end; i++) movieShelf.addView(vaultBox(vaultItems.get(i)));
        if (nextMovieOffset >= 0 && end == vaultItems.size()) {
            moreMovies = focusButton("LOAD MORE MOVIES");
            moreMovies.setOnClickListener(v -> loadMoviePage(creator, nextMovieOffset));
            movieShelf.addView(moreMovies, new LinearLayout.LayoutParams(-2, -1));
        }
        if (moveFocus && start < movieShelf.getChildCount()) movieShelf.getChildAt(start).requestFocus();
    }

    private View vaultBox(VaultItem item) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(9), dp(5), dp(5), dp(6));
        box.setFocusable(true);
        int[] sleeve = {color("#534250"), color("#191320"), color("#47312B")};
        box.setBackground(surface(sleeve, color("#B08B64"), 1, 6));
        ImageView poster = new ImageView(this);
        poster.setScaleType(ImageView.ScaleType.CENTER_CROP);
        poster.setBackgroundColor(color("#201528"));
        box.addView(poster, new LinearLayout.LayoutParams(-1, dp(138)));
        TextView label = text("CF HOME VIDEO  •  VHS", 8, true);
        label.setTextColor(color("#FFE0A7"));
        box.addView(label);
        TextView title = text(item.title + (item.year.isEmpty() ? "" : " (" + item.year + ")"), 12, true);
        title.setMaxLines(2);
        box.addView(title, new LinearLayout.LayoutParams(-1, dp(42)));
        LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(dp(112), -2);
        layout.setMargins(dp(5), dp(6), dp(5), dp(5));
        box.setLayoutParams(layout);
        box.setOnFocusChangeListener((view, focused) -> {
            boolean reduceMotion = Settings.Global.getFloat(getContentResolver(),
                    Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f;
            float scale = focused && !reduceMotion ? 1.055f : 1f;
            view.animate().scaleX(scale).scaleY(scale).translationY(focused && !reduceMotion ? -dp(3) : 0)
                    .setDuration(reduceMotion ? 0 : 170).start();
            view.setBackground(surface(sleeve, color(focused ? "#FF68D7" : "#B08B64"), focused ? 3 : 1, 6));
        });
        box.setOnClickListener(view -> new AlertDialog.Builder(this).setTitle(item.title)
                .setPositiveButton("Insert movie", (dialog, which) -> playUrl(item.title, item.url))
                .setNegativeButton("Back", null).show());
        if (!item.poster.isEmpty()) posterLoader.execute(() -> loadPoster(item.poster, poster));
        return box;
    }

    private void loadPoster(String address, ImageView poster) {
        HttpURLConnection connection = null;
        try {
            URL url = new URL(address);
            if (!"https".equals(url.getProtocol())) return;
            connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(5000);
            BitmapFactory.Options options = new BitmapFactory.Options();
            options.inSampleSize = 2;
            try (InputStream input = connection.getInputStream()) {
                android.graphics.Bitmap image = BitmapFactory.decodeStream(input, null, options);
                if (image != null) runOnUiThread(() -> {
                    if (poster.isAttachedToWindow()) poster.setImageBitmap(image);
                });
            }
        } catch (Exception ignored) {
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private void selectUpdate() {
        if (updateBusy) return;
        if (pendingUpdate == null) {
            checkUpdates(true);
        } else if (canInstallUpdates()) {
            downloadUpdate();
        } else {
            new AlertDialog.Builder(this)
                    .setTitle("Allow ChannelForge TV to install updates")
                    .setMessage("In Fire TV settings, turn on Install Unknown Apps for ChannelForge TV. Then return here to install the update.")
                    .setNegativeButton("Cancel", null)
                    .setPositiveButton("Open settings", (dialog, which) -> openInstallSettings())
                    .show();
        }
    }

    private boolean canInstallUpdates() {
        return Build.VERSION.SDK_INT < 26 || getPackageManager().canRequestPackageInstalls();
    }

    private void openInstallSettings() {
        try {
            Intent settings = new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:" + getPackageName()));
            startActivityForResult(settings, INSTALL_SETTINGS_REQUEST);
        } catch (ActivityNotFoundException | SecurityException unsupported) {
            showBanner("Open Fire TV Settings → My Fire TV → Developer Options → Install Unknown Apps. Allow ChannelForge TV, then select Updates again.");
        }
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != INSTALL_SETTINGS_REQUEST || pendingUpdate == null || updateBusy) return;
        if (canInstallUpdates()) downloadUpdate();
        else showBanner("Allow ChannelForge TV to install unknown apps, then select Updates again.");
    }

    private void checkUpdates(boolean requested) {
        updateBusy = true;
        updateButton.setText("UPDATES  •  CHECKING…");
        updateLoader.execute(() -> {
            try {
                FireTvUpdate release = FireTvUpdate.latest();
                runOnUiThread(() -> {
                    if (isDestroyed()) return;
                    pendingUpdate = release;
                    finishUpdate(requested ? (release == null ? "ChannelForge TV is up to date."
                            : "Select Update to install version " + release.version + ".") : "");
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    if (isDestroyed()) return;
                    updateBusy = false;
                    updateButton.setText("UPDATES  •  RETRY");
                    if (requested) showBanner("Could not check for updates. Select Updates to try again.");
                });
            }
        });
    }

    private void downloadUpdate() {
        FireTvUpdate release = pendingUpdate;
        updateBusy = true;
        updateButton.setText("DOWNLOADING UPDATE…");
        updateLoader.execute(() -> {
            try {
                int session = release.stage(getApplicationContext(), percent -> runOnUiThread(() -> {
                    if (!isDestroyed()) updateButton.setText(percent == 100 ? "VERIFYING UPDATE…"
                            : "DOWNLOADING  •  " + percent + "%");
                }));
                runOnUiThread(() -> {
                    if (isDestroyed() || isFinishing()) {
                        abandonUpdate(session);
                        return;
                    }
                    updateSession = session;
                    updateButton.setText("UPDATE READY");
                    commitUpdate();
                });
            } catch (Exception error) {
                String message = error instanceof IOException ? error.getMessage()
                        : "Could not prepare the update. Select Updates to try again.";
                runOnUiThread(() -> {
                    if (!isDestroyed()) finishUpdate(message);
                });
            }
        });
    }

    private void commitUpdate() {
        if (!resumed || updateSession < 0 || sessionCommitted) return;
        PackageInstaller installer = getPackageManager().getPackageInstaller();
        Intent callback = new Intent(UPDATE_RESULT).setPackage(getPackageName());
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 31) flags |= PendingIntent.FLAG_MUTABLE;
        PendingIntent result = PendingIntent.getBroadcast(this, updateSession, callback, flags);
        try (PackageInstaller.Session session = installer.openSession(updateSession)) {
            session.commit(result.getIntentSender());
            sessionCommitted = true;
        } catch (IOException | RuntimeException error) {
            abandonUpdate(updateSession);
            finishUpdate("Fire TV could not open the update installer. Try again.");
        }
    }

    private void openInstallConfirmation() {
        if (!resumed || installConfirmation == null) return;
        Intent confirmation = installConfirmation;
        installConfirmation = null;
        try {
            updateButton.setText("CONFIRM INSTALL");
            startActivity(confirmation);
            installerOpened = true;
        } catch (ActivityNotFoundException | SecurityException error) {
            abandonUpdate(updateSession);
            finishUpdate("Fire TV could not open its installation prompt. Try again.");
        }
    }

    private void abandonUpdate(int sessionId) {
        try {
            getPackageManager().getPackageInstaller().abandonSession(sessionId);
        } catch (RuntimeException ignored) {
            // The installer can already have closed the session.
        }
    }

    private void finishUpdate(String message) {
        updateBusy = sessionCommitted = installerOpened = false;
        updateSession = -1;
        installConfirmation = null;
        updateButton.setText(pendingUpdate == null ? "UP TO DATE ✓  v" + BuildConfig.VERSION_NAME
                : "UPDATE TO v" + pendingUpdate.version);
        if (resumed && guideVisible && !message.isEmpty()) showBanner(message);
    }

    private void scheduleLive(long delay) {
        video.removeCallbacks(continueLive);
        video.removeCallbacks(upcomingProgram);
        video.removeCallbacks(liveProgramBoundary);
        video.postDelayed(continueLive, delay);
    }

    private void retuneAtBoundary() {
        if (tunedChannel == null || guideVisible) return;
        showBuffering("Loading the next program…", true);
        scheduleLive(Math.max(350, tunedProgramEnd - System.currentTimeMillis() + 200));
    }

    private void trackLiveProgram() {
        video.removeCallbacks(upcomingProgram);
        video.removeCallbacks(liveProgramBoundary);
        tunedProgramEnd = 0;
        hasUpcomingProgram = false;
        long now = System.currentTimeMillis();
        if (tunedChannel == null || tunedChannel.programs == null) return;
        for (int i = 0; i < tunedChannel.programs.length(); i++) {
            JSONObject program = tunedChannel.programs.optJSONObject(i);
            if (program == null) continue;
            long start = guideTimeMillis(program, "start");
            long end = guideTimeMillis(program, "stop");
            if (start <= now && now < end) {
                tunedProgramEnd = end;
                video.postDelayed(liveProgramBoundary, end - now + 1700);
                if (i + 1 < tunedChannel.programs.length()) {
                    hasUpcomingProgram = true;
                    long lead = Math.min(12_000L, (end - start) / 3);
                    video.postDelayed(upcomingProgram, Math.max(100L, end - now - lead));
                }
                return;
            }
        }
    }

    private void refreshOnAirGuide(Channel channel) {
        if (channel.guideUrl.isEmpty()) return;
        studioLoader.execute(() -> {
            try {
                for (Channel updated : parseOnlineChannels(get(channel.guideUrl), channel.guideUrl)) {
                    if (!channel.url.equals(updated.url)) continue;
                    runOnUiThread(() -> {
                        if (isFinishing() || isDestroyed() || tunedChannel != channel || guideVisible) return;
                        channel.programs = updated.programs;
                        trackLiveProgram();
                        if (player.isPlaying()) showChannelBanner(channel);
                    });
                    break;
                }
            } catch (Exception ignored) {}
        });
    }

    private void waitForPlayback() {
        playbackFailed = false;
        if (playbackWaiting) return;
        playbackWaiting = true;
        video.postDelayed(playbackDeadline, 24_000L);
    }

    private void clearPlaybackDeadline() {
        playbackWaiting = false;
        video.removeCallbacks(playbackDeadline);
    }

    private void hidePlaybackBuffer() {
        clearPlaybackDeadline();
        bufferPanel.setVisibility(View.GONE);
        liveRetries = 0;
    }

    private void startStream(String url, boolean live) {
        renderedFrame = false;
        MediaItem.Builder media = new MediaItem.Builder().setUri(url).setMimeType(MimeTypes.APPLICATION_M3U8);
        if (live) {
            media.setLiveConfiguration(new MediaItem.LiveConfiguration.Builder().setTargetOffsetMs(3200).build());
            player.setMediaItem(media.build());
        } else {
            player.setMediaItem(media.build(), 0);
        }
        player.prepare();
        player.play();
    }

    private void failPlayback() {
        playbackFailed = true;
        showBuffering("The studio could not deliver video. Press OK to retry or Back for the guide.", false);
    }

    private void play(int index) {
        if (channels.isEmpty()) return;
        channelIndex = (index + channels.size()) % channels.size();
        tunedChannel = channels.get(channelIndex);
        streamTitle = tunedChannel.name;
        liveRetries = 0;
        clearPlaybackDeadline();
        activeMovieUrl = "";
        guide.setVisibility(View.GONE);
        guideVisible = false;
        video.hideController();
        video.setUseController(false);
        video.setFocusable(true);
        video.requestFocus();
        video.removeCallbacks(continueLive);
        continueLive.run();
        showChannelBanner(tunedChannel);
    }

    private void playUrl(String title, String url) {
        tunedChannel = null;
        video.removeCallbacks(upcomingProgram);
        video.removeCallbacks(liveProgramBoundary);
        streamTitle = title;
        activeMovieUrl = url;
        clearPlaybackDeadline();
        video.removeCallbacks(continueLive);
        guide.setVisibility(View.GONE);
        guideVisible = false;
        video.setUseController(true);
        video.setFocusable(true);
        video.requestFocus();
        showBuffering("Connecting to the studio…", true);
        waitForPlayback();
        startStream(url, false);
        showBanner("REWIND CINEMA   •   " + title);
    }
    private String displayNumber(Channel channel, int index) {
        return channel.number.isEmpty() ? String.format(Locale.US, "%03d", index + 1) : channel.number;
    }

    private void showChannelBanner(Channel channel) {
        String message = "CH " + displayNumber(channel, channelIndex) + "   " + channel.name;
        long now = System.currentTimeMillis();
        if (channel.programs != null) for (int i = 0; i < channel.programs.length(); i++) {
            JSONObject program = channel.programs.optJSONObject(i);
            if (program == null) continue;
            long start = guideTimeMillis(program, "start");
            long stop = guideTimeMillis(program, "stop");
            if (start > now || stop <= now) continue;
            message += "\nNOW  " + program.optString("title");
            JSONObject next = channel.programs.optJSONObject(i + 1);
            if (next != null) message += "\nCOMING UP NEXT  " + next.optString("title");
            break;
        }
        showBanner(message, channel.logo);
    }

    private long guideTimeMillis(JSONObject program, String field) {
        long milliseconds = program.optLong(field + "_epoch_ms");
        if (milliseconds > 0) return milliseconds;
        String utc = program.optString(field);
        if (utc.length() < 19) return 0;
        try {
            java.text.SimpleDateFormat format = new java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US);
            format.setTimeZone(TimeZone.getTimeZone("UTC"));
            return format.parse(utc.substring(0, 19)).getTime();
        } catch (java.text.ParseException error) {
            return 0;
        }
    }

    private void showBuffering(String message, boolean loading) {
        bufferLabel.setText(message + (streamTitle.isEmpty() ? "" : "\n" + streamTitle));
        bufferSpinner.setVisibility(loading ? View.VISIBLE : View.GONE);
        bufferPanel.setVisibility(View.VISIBLE);
    }

    private void showBanner(String message) { showBanner(message, ""); }

    private void showBanner(String message, String logo) {
        bannerPanel.removeAllViews();
        if (!logo.isEmpty()) {
            ImageView mark = new ImageView(this);
            mark.setScaleType(ImageView.ScaleType.FIT_CENTER);
            bannerPanel.addView(mark, new LinearLayout.LayoutParams(dp(55), dp(47)));
            posterLoader.execute(() -> loadPoster(logo, mark));
        }
        banner.setText(message);
        bannerPanel.addView(banner, new LinearLayout.LayoutParams(0, -2, 1));
        bannerPanel.setVisibility(View.VISIBLE);
        bannerPanel.removeCallbacks(hideBanner);
        bannerPanel.postDelayed(hideBanner, 5000);
    }

    private final Runnable hideBanner = () -> bannerPanel.setVisibility(View.GONE);

    @Override public boolean onKeyDown(int key, KeyEvent event) {
        if (!guideVisible) {
            boolean select = key == KeyEvent.KEYCODE_DPAD_CENTER || key == KeyEvent.KEYCODE_ENTER;
            if (select && playbackFailed) {
                if (tunedChannel != null) play(channelIndex);
                else if (!activeMovieUrl.isEmpty()) playUrl(streamTitle, activeMovieUrl);
                return true;
            }
            if (key == KeyEvent.KEYCODE_BACK || (select && tunedChannel != null)) {
                returnToGuide();
                channelRail.requestFocus();
                return true;
            }
            if (select) {
                video.showController();
                return true;
            }
            if (tunedChannel != null && (key == KeyEvent.KEYCODE_DPAD_UP || key == KeyEvent.KEYCODE_CHANNEL_UP)) {
                play(channelIndex + 1);
                return true;
            }
            if (tunedChannel != null && (key == KeyEvent.KEYCODE_DPAD_DOWN || key == KeyEvent.KEYCODE_CHANNEL_DOWN)) {
                play(channelIndex - 1);
                return true;
            }
        }
        return super.onKeyDown(key, event);
    }

    @Override protected void onResume() {
        super.onResume();
        resumed = true;
        if (guide != null) loadDirectory();
        commitUpdate();
        openInstallConfirmation();
    }
    @Override protected void onPause() {
        resumed = false;
        super.onPause();
    }
    @Override protected void onStop() {
        returnToGuide();
        super.onStop();
    }

    private void returnToGuide() {
        tunedChannel = null;
        activeMovieUrl = "";
        playbackFailed = false;
        clearPlaybackDeadline();
        video.hideController();
        video.removeCallbacks(continueLive);
        video.removeCallbacks(upcomingProgram);
        video.removeCallbacks(liveProgramBoundary);
        player.stop();
        video.setFocusable(false);
        guideVisible = true;
        guide.setVisibility(View.VISIBLE);
        bannerPanel.setVisibility(View.GONE);
        bufferPanel.setVisibility(View.GONE);
        streamTitle = "";
    }
    @Override protected void onDestroy() {
        unregisterReceiver(updateResult);
        updateLoader.shutdownNow();
        if (updateSession >= 0 && !installerOpened) abandonUpdate(updateSession);
        if (guide != null) {
            guide.removeCallbacks(directoryRefresh);
            guide.removeCallbacks(directoryRetry);
        }
        posterLoader.shutdownNow();
        studioLoader.shutdownNow();
        clearPlaybackDeadline();
        video.setPlayer(null);
        player.release();
        super.onDestroy();
    }

    private static class Creator {
        final String name, description, playlist, vault, channelStatus;
        Creator(String name, String description, String playlist, String vault, String channelStatus) {
            this.name = name;
            this.description = description;
            this.playlist = playlist;
            this.vault = vault;
            this.channelStatus = channelStatus;
        }
    }
    private static class Channel {
        final String name, url;
        String number = "", logo = "", guideUrl = "";
        JSONArray programs;
        Channel(String name, String url) { this.name = name; this.url = url; }
    }
    private static class VaultItem { final String title,year,summary,poster,url; VaultItem(String t,String y,String s,String p,String u){title=t;year=y;summary=s;poster=p;url=u;} }
}
