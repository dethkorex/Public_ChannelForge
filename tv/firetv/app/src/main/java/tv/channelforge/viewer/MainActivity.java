package tv.channelforge.viewer;

import android.animation.Animator;
import android.animation.AnimatorListenerAdapter;
import android.animation.ValueAnimator;
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
import android.content.pm.ActivityInfo;
import android.content.pm.PackageInstaller;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Typeface;
import android.graphics.drawable.BitmapDrawable;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.LayerDrawable;
import android.media.SoundPool;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.SystemClock;
import android.provider.Settings;
import android.text.SpannableString;
import android.text.TextUtils;
import android.text.style.ForegroundColorSpan;
import android.util.LruCache;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.widget.*;
import androidx.media3.common.AudioAttributes;
import androidx.media3.common.C;
import androidx.media3.common.Format;
import androidx.media3.common.MediaItem;
import androidx.media3.common.MimeTypes;
import androidx.media3.common.PlaybackException;
import androidx.media3.common.Player;
import androidx.media3.common.Tracks;
import androidx.media3.common.util.UnstableApi;
import androidx.media3.datasource.DefaultHttpDataSource;
import androidx.media3.datasource.HttpDataSource;
import androidx.media3.exoplayer.DefaultLoadControl;
import androidx.media3.exoplayer.DefaultRenderersFactory;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory;
import androidx.media3.exoplayer.source.LoadEventInfo;
import androidx.media3.exoplayer.source.MediaLoadData;
import androidx.media3.exoplayer.upstream.DefaultBandwidthMeter;
import androidx.media3.ui.PlayerView;
import androidx.media3.ui.AspectRatioFrameLayout;
import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.text.Normalizer;
import java.util.*;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

@UnstableApi
public class MainActivity extends Activity {
    private String viewerLease;
    private SoundPool menuSounds;
    private int menuTick;
    private boolean menuSoundReady;
    private long lastMenuSound;
    private final List<Creator> creators = new ArrayList<>();
    private final List<Channel> channels = new ArrayList<>();
    private final List<VaultItem> vaultItems = new ArrayList<>();
    private String movieDecade = "all", movieSearch = "", movieGenre = "", movieRating = "";
    private JSONArray movieGenres = new JSONArray(), movieRatings = new JSONArray();
    private PlayerView video;
    private FrameLayout activityRoot;
    private View cassetteMotion;
    private Runnable revealMovie;
    private ValueAnimator cassetteAnimator;
    private boolean ejectingTape;
    private LinearLayout movieDetails;
    private TextView insertMovie;
    private TextView touchGuideButton;
    private boolean compactTouchLayout, touchDevice, phoneLayout, phonePortrait, tvTheme;
    private View phoneMenuShade, phoneContent, phoneHeader, phoneHeaderLine;
    private ExoPlayer player;
    private boolean renderedFrame;
    private enum Page { HOME, LIVE, CINEMA, CONTINUE, FAVORITES, SYSTEM }
    private Page activePage = Page.HOME;
    private Page renderedAuxiliary;
    private final Map<Page, TextView> navButtons = new EnumMap<>(Page.class);
    private LinearLayout guide, channelRail, navigationRail, menuPanel, auxiliaryPage;
    private boolean menuCollapsed;
    private int navigationWidth;

    private TextView status, banner, updateButton;
    private LinearLayout bufferPanel, bannerPanel;
    private ProgressBar bufferSpinner;
    private TextView bufferLabel;
    private TextView touchRetry, touchDebugger;
    private JSONObject lastPlaybackReport;
    private String streamTitle = "";
    private int channelIndex = 0;
    private boolean guideVisible = true;
    private Channel tunedChannel;
    private int liveRetries;
    private long tunedProgramEnd;
    private long transitionAt;
    private String transitionTitle = "";
    private boolean hasUpcomingProgram, upcomingDue, upcomingShown;
    private final Runnable upcomingProgram = () -> { upcomingDue = true; showUpcomingIfPlaying(); };
    private final Runnable liveProgramBoundary = () -> {
        if (tunedChannel == null || guideVisible) return;
        if (tunedChannel.continuousUrl.isEmpty()) {
            retuneAtBoundary();
        } else {
            trackLiveProgram();
            if (!hasUpcomingProgram) refreshOnAirGuide(tunedChannel);
            showChannelBanner(tunedChannel);
        }
    };
    private boolean playbackWaiting, playbackFailed, consumingRetryKey;
    private int temporaryBitrateCap = Integer.MAX_VALUE, rebufferCount;
    private long rebufferStarted, totalRebufferMs;
    private final ArrayDeque<Long> recentLiveStalls = new ArrayDeque<>();
    private boolean recovering;
    private boolean usingPreferredRelay, skipPreferredRelay;
    private final Runnable lowerQuality = this::lowerQualityDuringStall;
    private final Runnable resetPlaybackRetries = () -> {
        liveRetries = 0;
        movieRetries = 0;
        setBitrateCap(Integer.MAX_VALUE);
    };
    private VaultItem activeMovie;
    private boolean movieFinished;
    private int movieRetries, playGeneration;
    private long resumePosition, lastFrameChange, firstFrameMs = -1, tuneStarted;
    private long routeRecoveryStarted;
    private int lastFrameCount = -1, lastErrorCode, lastHttpCode, totalRetries;
    private int segmentLoads, segmentErrors, manifestErrors;
    private long segmentLoadMs, segmentLoadBytes, slowestSegmentMs, slowestManifestMs;
    private int relayCacheHits, relayCacheMisses;
    private long slowestRelayUpstreamMs = -1, slowestStudioResponseMs = -1;
    private String deliveryPath = "unknown";
    private JSONObject lastPublicPlayback;
    private String lastReason = "none", decoderName = "unknown", lastHttpResource;
    private long directoryChecked, lastCpuTime, lastCpuTick;
    private int cpuPercent = -1;
    private final ArrayDeque<String> recentEvents = new ArrayDeque<>();
    private final ArrayDeque<JSONObject> testerTimeline = new ArrayDeque<>();
    private static final String TESTER_CONSENT = TesterApplication.TESTER_CONSENT;
    private static final String TESTER_NOTICE = "This private alpha can buffer, stop, or crash. "
            + "Only agree if you want ChannelForge to collect app diagnostics on this device. "
            + "Use Developer to review the data and choose whether to send it. Reports include timing, "
            + "buffering, stream errors, and the device model. They exclude accounts and media titles. "
            + "This build provides no remote device access.";
    private String pendingReport;
    private static final int EXPORT_REPORT = 22;
    private DefaultBandwidthMeter bandwidth;
    private final Runnable watchdog = new Runnable() {
        @Override public void run() {
            if (guideVisible || playbackFailed || ejectingTape) return;
            long now = SystemClock.elapsedRealtime();
            androidx.media3.exoplayer.DecoderCounters counters = player.getVideoDecoderCounters();
            long cpu = android.os.Process.getElapsedCpuTime();
            if (lastCpuTick != 0 && lastCpuTick < now) cpuPercent = (int)((cpu - lastCpuTime) * 100 / (now - lastCpuTick));
            lastCpuTime = cpu; lastCpuTick = now;
            if (!player.isPlaying() || counters == null || player.getVideoFormat() == null) {
                lastFrameChange = now;
            } else {
                counters.ensureUpdated();
                if (counters.renderedOutputBufferCount != lastFrameCount) {
                    lastFrameCount = counters.renderedOutputBufferCount;
                    lastFrameChange = now;
                } else if (now - lastFrameChange >= 12_000) {
                    recover("Video decoder stalled");
                    return;
                }
            }
            video.postDelayed(this, 2000);
        }
    };
    private final LruCache<String, Bitmap> posters = new LruCache<String, Bitmap>(6 * 1024 * 1024) {
        @Override protected int sizeOf(String address, Bitmap image) { return image.getByteCount(); }
    };
    private final Runnable playbackDeadline = () -> recover("Studio did not deliver video before the deadline", true);
    private final Runnable continueLive = () -> {
        if (tunedChannel != null && !guideVisible) {
            trackLiveProgram();
            if (!hasUpcomingProgram) refreshOnAirGuide(tunedChannel);
            showBuffering("Connecting to the studio…", true);
            waitForPlayback();
            startStream(tunedChannel.liveUrl(liveRetries, phoneLayout), true);
        }
    };
    private boolean directoryLoading = false;
    private boolean readInitialCache = true;
    private boolean directoryStale;
    private final Set<String> staleGuides = new HashSet<>();
    private final Set<String> unreachableStudios = new HashSet<>();
    private GuideCache guideCache;
    private final ExecutorService posterLoader = Executors.newFixedThreadPool(3);
    private final ExecutorService movieCatalogLoader = Executors.newSingleThreadExecutor();
    private final ExecutorService movieCoverLoader = Executors.newSingleThreadExecutor();
    private final ExecutorService studioLoader = Executors.newFixedThreadPool(4);
    private final ExecutorService recoveryLoader = Executors.newSingleThreadExecutor();
    private final ExecutorService updateLoader = Executors.newSingleThreadExecutor();
    private static final String UPDATE_RESULT = BuildConfig.APPLICATION_ID + ".UPDATE_RESULT";
    private static final String UPDATE_PERMISSION = BuildConfig.APPLICATION_ID + ".permission.UPDATE_RESULT";
    private static final int INSTALL_SETTINGS_REQUEST = 21;
    private FireTvUpdate pendingUpdate;
    private String latestUpdateNotes = "";
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
    private final Map<String, List<Channel>> channelsByStudio = new HashMap<>();
    private final Set<String> loadingGuides = new HashSet<>();
    private String guideStudio = "", guideSearch = "";
    private boolean guideSortByRating;
    private int guideOffset;
    private boolean fullGuide;
    private LinearLayout homeLiveContent, homeCinemaContent, homePersonalContent;
    private String homeLiveKey = "", homeCinemaAddress = "", homePersonalKey;
    private List<VaultItem> homeCinemaMovies = Collections.emptyList();
    private TextView programTitle, programTime, programElapsed, programDuration;
    private ProgressBar programProgress;
    private ImageView programArtwork, landscapeProgramArtwork;
    private Channel previewChannel;
    private final Runnable previewClock = new Runnable() {
        @Override public void run() {
            if (!resumed || !guideVisible || activePage != Page.LIVE || previewChannel == null) return;
            updateProgram(previewChannel);
            programArtwork.postDelayed(this, phoneLayout ? 3000 : 1000);
        }
    };
    private TextView screenSwitcher;
    private boolean cinemaScreen;
    private Creator currentStudio;
    private int movieRequest = 0;
    private boolean moviePageLoading, movieShelfLoaded;
    private int nextMovieOffset = -1;
    private LinearLayout movieShelf;
    private TextView moreMovies;
    private String selectedPlaylist = "";
    private static final String PREFS = TesterUpdateReceiver.PREFS;
    private static final String PAIRED_CREATOR = "paired_creator_url";
    private static final long DIRECTORY_REFRESH_MS = 60_000L;
    private static final long DIRECTORY_RETRY_MS = 10_000L;
    private static final int MOVIE_PAGE_SIZE = 12;
    private static final long METADATA_DEADLINE_MS = 20_000L;
    private static final int MAX_METADATA_BYTES = 24 * 1024 * 1024;
    private final Runnable directoryRetry = this::loadDirectory;
    private final Runnable directoryRefresh = new Runnable() {
        @Override public void run() {
            if (guideVisible && resumed) loadDirectory();
            if (guide != null) guide.postDelayed(this, DIRECTORY_REFRESH_MS);
        }
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        menuSounds = new SoundPool.Builder().setMaxStreams(1)
                .setAudioAttributes(new android.media.AudioAttributes.Builder()
                        .setUsage(android.media.AudioAttributes.USAGE_ASSISTANCE_SONIFICATION)
                        .setContentType(android.media.AudioAttributes.CONTENT_TYPE_SONIFICATION).build()).build();
        menuSounds.setOnLoadCompleteListener((pool, sample, status) -> menuSoundReady = status == 0);
        menuTick = menuSounds.load(this, R.raw.navigation, 1);
        PackageManager features = getPackageManager();
        phoneLayout = BuildConfig.PHONE_TESTER || (!BuildConfig.TESTER_BUILD
                && features.hasSystemFeature(PackageManager.FEATURE_TOUCHSCREEN)
                && !features.hasSystemFeature(PackageManager.FEATURE_LEANBACK)
                && !features.hasSystemFeature("android.hardware.type.television"));
        tvTheme = !phoneLayout && (BuildConfig.TESTER_BUILD || features.hasSystemFeature("amazon.hardware.fire_tv")
                || features.hasSystemFeature(PackageManager.FEATURE_LEANBACK)
                || features.hasSystemFeature("android.hardware.type.television"));
        if (phoneLayout) {
            if (!BuildConfig.PHONE_TESTER) setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_FULL_USER);
            getWindow().clearFlags(android.view.WindowManager.LayoutParams.FLAG_FULLSCREEN);
        } else getWindow().getDecorView().setSystemUiVisibility(5894);
        guideCache = new GuideCache(getFilesDir());
        buildUi();
        registerUpdateResult();
        if (BuildConfig.TESTER_BUILD && !testerConsented()) {
            new AlertDialog.Builder(this).setTitle("ChannelForge TV Tester").setMessage(TESTER_NOTICE)
                    .setCancelable(false).setNegativeButton("Decline", (box, button) -> finish())
                    .setPositiveButton("I agree", (box, button) -> {
                        getSharedPreferences(PREFS, MODE_PRIVATE).edit().putBoolean(TESTER_CONSENT, true).apply();
                        startViewer();
                    }).show();
        } else startViewer();
    }

    private void startViewer() {
        loadDirectory();
        guide.postDelayed(directoryRefresh, DIRECTORY_REFRESH_MS);
        if (!BuildConfig.TESTER_BUILD) checkUpdates(false);
        if (BuildConfig.TESTER_BUILD) updateLoader.execute(((TesterApplication) getApplication())::sendPendingCrash);
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
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextColor(Color.WHITE);
        view.setTextSize(Math.max(9, size - 1));
        view.setPadding(dp(11), dp(5), dp(11), dp(5));
        if (bold) view.setTypeface(Typeface.DEFAULT_BOLD);
        return view;
    }

    private int dp(int value){return Math.round(value*getResources().getDisplayMetrics().density);}
    private int color(String value){return Color.parseColor(value);}
    private GradientDrawable surface(int[] colors,int strokeColor,int strokeWidth,int radius){GradientDrawable value=new GradientDrawable(GradientDrawable.Orientation.TL_BR,colors);value.setCornerRadius(dp(radius));if(strokeWidth>0)value.setStroke(dp(strokeWidth),strokeColor);return value;}
    private LinearLayout panel(){LinearLayout value=new LinearLayout(this);value.setOrientation(LinearLayout.VERTICAL);value.setPadding(dp(10),dp(9),dp(10),dp(9));value.setBackground(surface(new int[]{color("#101938"),color("#090B20")},color("#174F75"),1,10));return value;}
    private TextView sectionTitle(String value,String accent){TextView title=text(value,14,true);title.setTextColor(color(accent));title.setLetterSpacing(.10f);return title;}
    private void addGap(LinearLayout parent,int height){Space gap=new Space(this);parent.addView(gap,new LinearLayout.LayoutParams(1,dp(height)));}

    private void buildUi() {
        FrameLayout root = new FrameLayout(this); activityRoot = root; root.setBackground(surface(new int[]{color("#050616"),color("#25073D"),color("#070312")},0,0,0));
        video = new PlayerView(this);
        DefaultHttpDataSource.Factory http = new DefaultHttpDataSource.Factory()
                .setUserAgent("ChannelForge-TV/" + BuildConfig.VERSION_NAME)
                .setConnectTimeoutMs(6000).setReadTimeoutMs(10000);
        // Start around SD until the first transfers yield an estimate for this network.
        bandwidth = new DefaultBandwidthMeter.Builder(this)
                .setInitialBitrateEstimate(phoneLayout ? 380_000L : 1_000_000L).build();
        player = new ExoPlayer.Builder(this, new DefaultRenderersFactory(this).setEnableDecoderFallback(true))
                .setMediaSourceFactory(new DefaultMediaSourceFactory(http))
                .setLoadControl(new DefaultLoadControl.Builder().setBufferDurationsMs(20000, 45000, 3500, 6000).build())
                .setBandwidthMeter(bandwidth)
                .setAudioAttributes(AudioAttributes.DEFAULT, true)
                .build();
        player.setTrackSelectionParameters(player.getTrackSelectionParameters().buildUpon()
                .setPreferredAudioLanguage("en").build());
        player.addAnalyticsListener(new androidx.media3.exoplayer.analytics.AnalyticsListener() {
            private final androidx.media3.common.Timeline.Window eventWindow = new androidx.media3.common.Timeline.Window();

            private boolean currentLoad(EventTime time) {
                if (guideVisible || viewerLease == null || player.getCurrentMediaItem() == null) return false;
                return time.timeline.isEmpty() || viewerLease.equals(time.timeline.getWindow(time.windowIndex, eventWindow).mediaItem.mediaId);
            }

            @Override public void onVideoDecoderInitialized(EventTime time, String name, long timestamp, long duration) {
                decoderName = name;
            }

            @Override public void onLoadCompleted(EventTime time, LoadEventInfo load, MediaLoadData media) {
                if (!currentLoad(time)) return;
                if (media.dataType == C.DATA_TYPE_MEDIA && load.bytesLoaded > 0) {
                    segmentLoads++;
                    segmentLoadBytes += load.bytesLoaded;
                    segmentLoadMs += load.loadDurationMs;
                    slowestSegmentMs = Math.max(slowestSegmentMs, load.loadDurationMs);
                } else if (media.dataType == C.DATA_TYPE_MANIFEST) {
                    slowestManifestMs = Math.max(slowestManifestMs, load.loadDurationMs);
                } else {
                    return;
                }
                recordDeliveryHeaders(load.responseHeaders);
            }

            @Override public void onLoadError(EventTime time, LoadEventInfo load, MediaLoadData media,
                                              IOException error, boolean canceled) {
                if (!currentLoad(time)) return;
                if (media.dataType == C.DATA_TYPE_MEDIA) segmentErrors++;
                else if (media.dataType == C.DATA_TYPE_MANIFEST) manifestErrors++;
                if (error instanceof androidx.media3.datasource.HttpDataSource.InvalidResponseCodeException) {
                    androidx.media3.datasource.HttpDataSource.InvalidResponseCodeException http =
                            (androidx.media3.datasource.HttpDataSource.InvalidResponseCodeException) error;
                    lastHttpCode = http.responseCode;
                    lastHttpResource = media.dataType == C.DATA_TYPE_MANIFEST ? "manifest" :
                            media.dataType == C.DATA_TYPE_MEDIA ? "segment" : "other";
                    recordDeliveryHeaders(http.headerFields);
                }
            }
        });
        video.setPlayer(player);
        video.setResizeMode(AspectRatioFrameLayout.RESIZE_MODE_FIT);
        video.setUseController(false);
        video.setKeepContentOnPlayerReset(true);
        video.setFocusable(false);
        video.setVisibility(View.GONE);
        root.addView(video, new FrameLayout.LayoutParams(-1, -1));
        int deviceMode = getResources().getConfiguration().uiMode & android.content.res.Configuration.UI_MODE_TYPE_MASK;
        touchDevice = !tvTheme && deviceMode != android.content.res.Configuration.UI_MODE_TYPE_TELEVISION
                && getPackageManager().hasSystemFeature(android.content.pm.PackageManager.FEATURE_TOUCHSCREEN);
        buildGuideUi(root);
        if (touchDevice) {
            touchGuideButton = focusButton("←  LIVE TV GUIDE");
            touchGuideButton.setContentDescription("Stop live TV and return to the guide");
            touchGuideButton.setMinHeight(dp(48));
            touchGuideButton.setVisibility(View.GONE);
            touchGuideButton.setOnClickListener(view -> returnToGuide());
            FrameLayout.LayoutParams liveNavigation = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.END);
            liveNavigation.setMargins(dp(12), dp(12), dp(12), 0);
            root.addView(touchGuideButton, liveNavigation);
        }
        bufferPanel = panel();
        bufferPanel.setGravity(Gravity.CENTER);
        bufferSpinner = new ProgressBar(this);
        bufferPanel.addView(bufferSpinner, new LinearLayout.LayoutParams(dp(38), dp(38)));
        bufferLabel = text("", 18, true);
        bufferLabel.setGravity(Gravity.CENTER);
        bufferPanel.addView(bufferLabel, new LinearLayout.LayoutParams(-1, -2));
        if (phoneLayout) {
            touchRetry = focusButton("↻  Retry");
            touchRetry.setOnClickListener(v -> retrySelection());
            bufferPanel.addView(touchRetry);
            touchDebugger = focusButton("Developer");
            touchDebugger.setOnClickListener(v -> showTesterTools());
            bufferPanel.addView(touchDebugger);
        }
        bufferPanel.setVisibility(View.GONE);
        root.addView(bufferPanel, new FrameLayout.LayoutParams(playbackPanelWidth(), -2, Gravity.CENTER));
        bannerPanel = new LinearLayout(this);
        bannerPanel.setGravity(Gravity.CENTER_VERTICAL);
        bannerPanel.setPadding(dp(12), dp(8), dp(12), dp(8));
        bannerPanel.setBackground(surface(new int[]{color("#111938"),color("#290A3E")},color("#12DFF9"),2,12));
        banner = text("", 18, true);
        bannerPanel.setVisibility(View.GONE);
        FrameLayout.LayoutParams bp = new FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM);
        bp.setMargins(dp(35), 0, dp(35), dp(35));
        root.addView(bannerPanel, bp);
        TextView eject = focusButton("⏏  EJECT MOVIE");
        FrameLayout.LayoutParams ejectLayout = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.RIGHT);
        ejectLayout.setMargins(0, dp(12), dp(16), 0);
        eject.setVisibility(View.GONE);
        eject.setOnClickListener(v -> ejectMovie());
        root.addView(eject, ejectLayout);
        video.setControllerVisibilityListener((PlayerView.ControllerVisibilityListener) visibility ->
                eject.setVisibility(visibility == View.VISIBLE && activeMovie != null && !ejectingTape
                        && cassetteMotion == null ? View.VISIBLE : View.GONE));
        if (phoneLayout) root.setFitsSystemWindows(true);
        setContentView(root);
        if (tvTheme) focusHomeNavigation();
        else if (!phoneLayout) screenSwitcher.requestFocus();
        player.addListener(new Player.Listener() {
            @Override public void onEvents(Player source, Player.Events events) {
                if (events.containsAny(Player.EVENT_PLAY_WHEN_READY_CHANGED, Player.EVENT_PLAYBACK_STATE_CHANGED,
                        Player.EVENT_PLAYBACK_SUPPRESSION_REASON_CHANGED)) updatePlaybackScreenAwake();
            }

            @Override public void onPlaybackStateChanged(int state) {
                if (state == Player.STATE_BUFFERING) testerEvent("buffering");
                if (state == Player.STATE_ENDED) testerEvent("ended");
                if (guideVisible || recovering || ejectingTape || playbackFailed) return;
                if (state == Player.STATE_BUFFERING) {
                    if (renderedFrame && player.getPlayWhenReady() && rebufferStarted == 0) {
                        rebufferStarted = SystemClock.elapsedRealtime();
                        rebufferCount++;
                        video.postDelayed(lowerQuality, 2500);
                    }
                    showBuffering("Buffering video…", true);
                    waitForPlayback();
                } else if (state == Player.STATE_READY) {
                    long stalledFor = finishRebuffer();
                    if (tunedChannel != null && player.getPlayWhenReady() && stalledFor >= 2000 && liveStallsRepeated()) {
                        recover("Live TV keeps buffering");
                        return;
                    }
                    if (revealMovie != null) revealMovie.run();
                    if (!player.getPlayWhenReady()) clearPlaybackDeadline();
                } else if (state == Player.STATE_ENDED) {
                    clearPlaybackDeadline();
                    if (tunedChannel != null && tunedChannel.airUntil > 0 && tunedChannel.airUntil <= System.currentTimeMillis() + 1000) {
                        showBuffering("Broadcast finished. Press Back for the guide.", false);
                    } else if (tunedChannel != null) {
                        if (tunedProgramEnd > System.currentTimeMillis() + 2500 || tunedProgramEnd == 0)
                            recover("Live stream ended before the scheduled transition");
                        else retuneAtBoundary();
                    } else if (activeMovie != null) {
                        movieFinished = true;
                        ejectMovie();
                    } else returnToGuide();
                }
            }

            @Override public void onRenderedFirstFrame() {
                if (guideVisible || recovering || ejectingTape || playbackFailed) return;
                renderedFrame = true;
                if (firstFrameMs < 0) {
                    firstFrameMs = SystemClock.elapsedRealtime() - tuneStarted;
                    testerEvent("first-frame");
                }
                if (BuildConfig.TESTER_BUILD) android.util.Log.i("ChannelForgePlayback",
                        usingPreferredRelay ? "First frame via preferred media relay" : "First frame via registered media route");
                if (player.getPlayWhenReady()) hidePlaybackBuffer();
                if (tunedChannel != null) showChannelBanner(tunedChannel);
                showUpcomingIfPlaying();
            }

            @Override public void onIsPlayingChanged(boolean playing) {
                if (playing && renderedFrame && !guideVisible && !recovering && !ejectingTape && !playbackFailed) {
                    hidePlaybackBuffer();
                    showUpcomingIfPlaying();
                } else if (!playing) video.removeCallbacks(resetPlaybackRetries);
            }

            @Override public void onPlayerError(PlaybackException error) {
                if (guideVisible || recovering || ejectingTape || playbackFailed) return;
                lastErrorCode = error.errorCode;
                Throwable cause = error.getCause();
                while (cause != null && !(cause instanceof HttpDataSource.InvalidResponseCodeException))
                    cause = cause.getCause();
                lastHttpCode = 0;
                lastHttpResource = null;
                if (cause instanceof HttpDataSource.InvalidResponseCodeException) {
                    HttpDataSource.InvalidResponseCodeException failure = (HttpDataSource.InvalidResponseCodeException) cause;
                    lastHttpCode = failure.responseCode;
                    String path = failure.dataSpec.uri.getPath();
                    lastHttpResource = path != null && path.endsWith(".ts") ? "segment"
                            : path != null && path.endsWith(".m3u8") ? "manifest" : "other";
                }
                String reason = lastHttpCode == 503 || lastHttpCode == 429 ? "Studio is busy"
                        : lastHttpCode == 502 || lastHttpCode == 504 ? "Studio relay is unavailable"
                        : lastHttpCode == 404 || lastHttpCode == 410 ? "Studio address or media expired"
                        : error.errorCode == PlaybackException.ERROR_CODE_BEHIND_LIVE_WINDOW ? "Live window moved"
                        : error.errorCode >= 4000 && error.errorCode < 5000 ? "Video decoder error"
                        : "Studio connection interrupted";
                recover(reason);
            }
        });
    }

    private void buildGuideUi(FrameLayout root) {
        navButtons.clear();
        phoneMenuShade = null;
        phoneContent = null;
        phoneHeader = phoneHeaderLine = null;
        int width = getResources().getConfiguration().screenWidthDp;
        int height = getResources().getConfiguration().screenHeightDp;
        phonePortrait = phoneLayout && (height > width || width < 540);
        compactTouchLayout = phoneLayout ? phonePortrait : touchDevice && width < 850;
        boolean compact = width < 800 || height < 450;
        guide = new LinearLayout(this); guide.setOrientation(LinearLayout.VERTICAL);
        int inset = dp(phoneLayout ? 10 : compact ? 14 : 19);
        guide.setPadding(inset,dp(9),inset,dp(compact ? 9 : 13));
        guide.setBackground(new GradientDrawable(GradientDrawable.Orientation.TL_BR,
                neonTheme() ? new int[]{color("#04091A"), color("#070C1C"), color("#190B35")}
                        : new int[]{color("#050F20"), color("#09152B"), color("#20103D")}));
        LinearLayout header=new LinearLayout(this);header.setGravity(Gravity.CENTER_VERTICAL);header.setPadding(0,0,0,dp(8));
        phoneHeader = header;
        TextView menu = focusButton("☰");
        menu.setContentDescription("Toggle navigation menu");
        menu.setPadding(dp(12), 0, dp(12), 0);
        menu.setOnClickListener(v -> toggleMenu());
        header.addView(menu);
        ImageView logo=new ImageView(this);
        logo.setImageResource(BuildConfig.TESTER_BUILD ? R.drawable.channel_forge_tv_tester_icon : R.drawable.channel_forge_viewer_brand);
        logo.setScaleType(ImageView.ScaleType.FIT_CENTER);
        int logoSize = dp(phoneLayout ? 48 : compact ? 36 : 44);
        logo.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        header.addView(logo,new LinearLayout.LayoutParams(logoSize,logoSize));
        LinearLayout brandBox=new LinearLayout(this);brandBox.setOrientation(LinearLayout.VERTICAL);
        TextView brand=text(BuildConfig.TESTER_BUILD && !phonePortrait ? "CHANNELFORGE TV TESTER" : "CHANNELFORGE TV",
                compact ? 16 : 20, true);
        brand.setTextColor(color("#FFF229"));brand.setLetterSpacing(compact ? .08f : .16f);
        brand.setSingleLine(true);
        brand.setEllipsize(android.text.TextUtils.TruncateAt.END);
        brand.setPadding(dp(compact ? 5 : 18), dp(3), dp(5), dp(3));
        if (neonTheme()) applyPhoneWordmark(brand, phonePortrait ? 16 : 26);
        if (phonePortrait) {
            brand.setSingleLine(true);
            brand.setEllipsize(TextUtils.TruncateAt.END);
        }
        brandBox.addView(brand);
        if (neonTheme()) brandBox.addView(phoneRule());
        else if (!compact) {
            TextView subtitle=text("THE COMMUNITY DIAL",11,true);
            subtitle.setTextColor(color("#55EFFF"));subtitle.setLetterSpacing(.28f);
            subtitle.setSingleLine(true);
            subtitle.setPadding(dp(18), 0, dp(5), dp(6));
            brandBox.addView(subtitle);
        }
        header.addView(brandBox,new LinearLayout.LayoutParams(0,-2,1));
        screenSwitcher=focusButton("REWIND CINEMA");
        screenSwitcher.setOnClickListener(v -> openCinemaScreen(!cinemaScreen));
        header.addView(screenSwitcher);
        screenSwitcher.setVisibility(phonePortrait ? View.GONE : View.VISIBLE);
        updateButton=focusButton(BuildConfig.TESTER_BUILD ? testerVersionLabel() : phonePortrait ? "v" + BuildConfig.VERSION_NAME : "UPDATES  •  v" + BuildConfig.VERSION_NAME);updateButton.setOnClickListener(v->selectUpdate());LinearLayout.LayoutParams updateLayout=new LinearLayout.LayoutParams(-2,-2);updateLayout.setMargins(dp(10),0,0,0);header.addView(updateButton,updateLayout);guide.addView(header);
        if (compact) {
            for (TextView control : new TextView[]{menu, screenSwitcher, updateButton}) {
                control.setTextSize(11);
                control.setSingleLine(true);
                control.setMinHeight(dp(34));
                control.setPadding(dp(9), 0, dp(9), 0);
            }
            updateButton.setMaxWidth(dp(170));
            updateButton.setEllipsize(android.text.TextUtils.TruncateAt.END);
        }
        if (phonePortrait) {
            menu.setMinWidth(dp(48));
            menu.setMinHeight(dp(48));
            menu.setTextSize(19);
            updateButton.setMinHeight(dp(48));
            updateButton.setGravity(Gravity.CENTER);
            updateButton.setSingleLine(false);
            updateButton.setMaxLines(2);
            updateButton.setMaxWidth(dp(106));
            updateButton.setTextSize(11);
            updateLayout.leftMargin = dp(4);
        }
        if (neonTheme()) {
            menu.setBackground(phonePanel(false, 10));
            updateButton.setBackground(phonePanel(false, 10));
            screenSwitcher.setBackground(phonePanel(true, 10));
        }
        if (tvTheme) {
            menu.setBackground(tvFrame(false, 11));
            updateButton.setBackground(tvFrame(false, 11));
            updateButton.setOnClickListener(v -> openPage(Page.SYSTEM));
            screenSwitcher.setBackground(tvFrame(false, 11));
        }
        menu.setId(View.generateViewId());
        screenSwitcher.setId(View.generateViewId());
        updateButton.setId(View.generateViewId());
        menu.setNextFocusRightId(screenSwitcher.getId());
        screenSwitcher.setNextFocusLeftId(menu.getId());
        screenSwitcher.setNextFocusRightId(updateButton.getId());
        updateButton.setNextFocusLeftId(screenSwitcher.getId());
        View line = neonTheme() ? phoneRule() : new View(this);
        if (!neonTheme()) line.setBackgroundColor(color("#16DFF5"));
        phoneHeaderLine = line;
        guide.addView(line,new LinearLayout.LayoutParams(-1,dp(2)));
        status = text(phonePortrait ? "Finding channels…" : "●  CONNECTING TO THE PUBLIC CHANNEL DIRECTORY…", 12, true);
        status.setTextColor(color("#77F6A3"));
        if (!phonePortrait) status.setBackground(surface(new int[]{color("#111A3B"),color("#0A0B20")},color("#225E8B"),1,8));
        LinearLayout.LayoutParams statusLayout = new LinearLayout.LayoutParams(-1, -2);
        statusLayout.setMargins(0, dp(6), 0, dp(6));
        if (tvTheme) {
            FrameLayout statusBar = new FrameLayout(this);
            statusBar.setBackground(tvFrame(false, 13));
            status.setBackground(null);
            status.setPadding(dp(14), dp(5), dp(52), dp(5));
            statusBar.addView(status, new FrameLayout.LayoutParams(-1, -2));
            FrameLayout.LayoutParams accent = new FrameLayout.LayoutParams(dp(52), dp(17), Gravity.END | Gravity.CENTER_VERTICAL);
            accent.rightMargin = dp(9);
            statusBar.addView(tvStripes(true), accent);
            guide.addView(statusBar, statusLayout);
        } else guide.addView(status, statusLayout);
        LinearLayout columns = new LinearLayout(this);
        FrameLayout phoneWorkspace = phonePortrait ? new FrameLayout(this) : null;
        guide.addView(phonePortrait ? phoneWorkspace : columns, new LinearLayout.LayoutParams(-1, 0, 1));
        menuPanel = new LinearLayout(this);
        menuPanel.setOrientation(LinearLayout.VERTICAL);
        menuPanel.setBackground(surface(new int[]{color("#0A0B1C"), color("#150B2B")}, color("#2D365B"), 1, 10));
        if (neonTheme()) menuPanel.setBackground(phonePanel(false, 11));
        if (tvTheme) menuPanel.setBackground(tvFrame(false, 14));
        navigationWidth = dp(phonePortrait ? Math.min(324, width - 27)
                : Math.max(compact ? 116 : 138, Math.min(180, width / 6)));
        LinearLayout.LayoutParams menuLayout = new LinearLayout.LayoutParams(navigationWidth, -1);
        menuLayout.setMargins(0, 0, dp(9), 0);
        if (!phonePortrait) columns.addView(menuPanel, menuLayout);
        ScrollView navigationScroll = new ScrollView(this);
        navigationScroll.setFillViewport(true);
        navigationScroll.setVerticalScrollBarEnabled(false);
        LinearLayout menuContent = new LinearLayout(this);
        menuContent.setPadding(dp(6), dp(5), dp(6), dp(5));
        menuContent.setOrientation(LinearLayout.VERTICAL);
        if (phonePortrait) addPhoneDrawerHeader(menuContent);
        navigationRail = new LinearLayout(this);
        navigationRail.setOrientation(LinearLayout.VERTICAL);
        menuContent.addView(navigationRail);
        if (phonePortrait) addPhoneBackdrop(menuContent, 113);
        navigationScroll.addView(menuContent);
        menuPanel.addView(navigationScroll, new LinearLayout.LayoutParams(-1, -1));
        addNavigation("⌂", "Home", Page.HOME);
        addNavigation(neonTheme() ? "▣" : "◉", "Live TV Guide", Page.LIVE);
        addNavigation("▣", "Rewind Cinema", Page.CINEMA);
        addNavigation("▶", "Continue Watching", Page.CONTINUE);
        addNavigation("★", "Favorites", Page.FAVORITES);
        addNavigation("⚙", "System", Page.SYSTEM);
        LinearLayout channelsPanel = panel();
        channelRail = new LinearLayout(this);
        channelRail.setOrientation(LinearLayout.VERTICAL);
        ScrollView channelScroll = new ScrollView(this);
        channelScroll.setVerticalScrollBarEnabled(false);
        channelScroll.addView(channelRail);
        channelsPanel.addView(channelScroll, new LinearLayout.LayoutParams(-1, 0, 1));
        if (phonePortrait) {
            channelsPanel.setPadding(0, dp(4), 0, 0);
            channelsPanel.setBackground(null);
            phoneContent = channelsPanel;
            phoneWorkspace.addView(channelsPanel, new FrameLayout.LayoutParams(-1, -1));
            phoneMenuShade = new View(this);
            phoneMenuShade.setBackgroundColor(color("#A0000611"));
            phoneMenuShade.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
            phoneMenuShade.setOnClickListener(v -> setMenuCollapsed(true));
            phoneWorkspace.addView(phoneMenuShade, new FrameLayout.LayoutParams(-1, -1));
            menuPanel.setClickable(true);
            phoneWorkspace.addView(menuPanel, new FrameLayout.LayoutParams(navigationWidth, -1, Gravity.START));
        } else {
            if (tvTheme) {
                channelsPanel.setPadding(0, 0, 0, 0);
                channelsPanel.setBackground(null);
            }
            columns.addView(channelsPanel, new LinearLayout.LayoutParams(0, -1, 1f));
        }
        syncStudioScreen();
        setMenuCollapsed(phoneLayout || !BuildConfig.TESTER_BUILD && compactTouchLayout);
        if (height >= 420 && !touchDevice) {
            TextView help = text(tvTheme ? "↑↓  Surf Channels     |     OK  Watch / Guide     |     ↶  Back"
                    : "↑↓  SURF CHANNELS     •     OK  WATCH / GUIDE     •     BACK  RETURN TO GUIDE", 10, true);
            help.setTextColor(color(tvTheme ? "#E2F3FF" : "#7184A9"));
            help.setSingleLine(true);
            if (tvTheme) {
                addGap(guide, 6);
                guide.addView(phoneRule());
                FrameLayout footer = new FrameLayout(this);
                help.setPadding(dp(6), dp(5), 0, dp(3));
                footer.addView(help);
                footer.addView(tvStripes(true), new FrameLayout.LayoutParams(dp(72), dp(19), Gravity.END | Gravity.BOTTOM));
                guide.addView(footer);
            } else guide.addView(help);
        }
        guide.setVisibility(guideVisible ? View.VISIBLE : View.GONE);
        root.addView(guide, 1, new FrameLayout.LayoutParams(-1,-1));
    }

    private GradientDrawable phonePanel(boolean purple, int radius) {
        return surface(new int[]{color(purple ? "#301348" : "#0D3148"), color("#080F24"),
                        color(purple ? "#1C0B2F" : "#061626")},
                color(purple ? "#D146F0" : "#29D9F9"), 1, radius);
    }

    private Drawable tvFrame(boolean purple, int radius) {
        GradientDrawable trim = surface(new int[]{color(purple ? "#F452FF" : "#38E7FF"), color("#E5F7FF"),
                color(purple ? "#D444F1" : "#08BEF4")}, 0, 0, radius);
        GradientDrawable fill = surface(new int[]{color(purple ? "#231038" : "#052B44"), color("#060C1C"),
                color(purple ? "#1E0C38" : "#061726")}, 0, 0, radius - 1);
        LayerDrawable layered = new LayerDrawable(new Drawable[]{trim, fill});
        int inset = dp(2);
        layered.setLayerInset(1, inset, inset, inset, inset);
        return layered;
    }

    private View tvStripes(boolean purple) {
        View stripes = new View(this) {
            private final android.graphics.Paint paint = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
            private final android.graphics.Path stripe = new android.graphics.Path();

            @Override protected void onDraw(android.graphics.Canvas canvas) {
                float step = getWidth() / 4f, rise = Math.min(getHeight(), step);
                paint.setColor(color(purple ? "#EB30FF" : "#29E5FF"));
                for (int index = 0; index < 3; index++) {
                    float x = index * step;
                    stripe.reset();
                    stripe.moveTo(x, getHeight());
                    stripe.lineTo(x + rise, 0);
                    stripe.lineTo(x + rise + step * .7f, 0);
                    stripe.lineTo(x + step * .7f, getHeight());
                    stripe.close();
                    paint.setAlpha(245 - 60 * index);
                    canvas.drawPath(stripe, paint);
                }
            }
        };
        stripes.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        return stripes;
    }

    private View tvHeading(String label, boolean purple) {
        LinearLayout section = new LinearLayout(this);
        section.setOrientation(LinearLayout.VERTICAL);
        LinearLayout row = new LinearLayout(this);
        row.setGravity(Gravity.CENTER_VERTICAL);
        TextView heading = sectionTitle(label, purple ? "#F2B5FF" : "#43F3FF");
        heading.setTypeface(Typeface.create("sans-serif-condensed", Typeface.BOLD));
        heading.setTextSize(label.length() > 14 ? 11 : 13);
        heading.setLetterSpacing(.05f);
        heading.setSingleLine(true);
        heading.setPadding(dp(6), dp(2), dp(2), dp(1));
        row.addView(heading, new LinearLayout.LayoutParams(0, -2, 1));
        row.addView(tvStripes(purple), new LinearLayout.LayoutParams(dp(31), dp(11)));
        section.addView(row);
        section.addView(phoneRule());
        return section;
    }

    private boolean neonTheme() {
        return phoneLayout || tvTheme;
    }

    private View phoneRule() {
        View line = new View(this);
        line.setBackground(new GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT,
                new int[]{color("#00AC41F0"), color("#DD48F5"), color("#29E3FA"), color("#0029E3FA")}));
        line.setLayoutParams(new LinearLayout.LayoutParams(-1, dp(2)));
        return line;
    }

    private void applyPhoneWordmark(TextView title, int size) {
        SpannableString brand = new SpannableString("ChannelForge TV");
        brand.setSpan(new ForegroundColorSpan(color("#13E5FA")), 13, 15, 0);
        title.setText(brand);
        title.setTextColor(Color.WHITE);
        title.setTypeface(Typeface.create("sans-serif-condensed", Typeface.BOLD_ITALIC));
        title.setTextSize(size);
        title.setLetterSpacing(0);
        title.setPadding(dp(5), dp(3), dp(3), dp(3));
        title.setSingleLine(!phonePortrait);
        title.setMaxLines(phonePortrait ? 2 : 1);
    }

    private ImageView phoneBackdrop() {
        ImageView art = new ImageView(this);
        art.setImageResource(R.drawable.cinema_hero);
        art.setScaleType(ImageView.ScaleType.CENTER_CROP);
        art.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        return art;
    }

    private void addPhoneBackdrop(LinearLayout column, int height) {
        addGap(column, 9);
        column.addView(phoneBackdrop(), new LinearLayout.LayoutParams(-1, dp(height)));
    }

    private void addPhoneDrawerHeader(LinearLayout column) {
        LinearLayout row = new LinearLayout(this);
        row.setGravity(Gravity.CENTER_VERTICAL);
        ImageView logo = new ImageView(this);
        logo.setImageResource(BuildConfig.TESTER_BUILD ? R.drawable.channel_forge_tv_tester_icon : R.drawable.channel_forge_viewer_brand);
        logo.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        row.addView(logo, new LinearLayout.LayoutParams(dp(46), dp(46)));
        TextView brand = text("", 16, true);
        applyPhoneWordmark(brand, 19);
        row.addView(brand, new LinearLayout.LayoutParams(0, -2, 1));
        TextView close = focusButton("×");
        close.setTextSize(22);
        close.setContentDescription("Close navigation menu");
        close.setMinHeight(dp(44));
        close.setBackgroundColor(Color.TRANSPARENT);
        close.setOnClickListener(v -> setMenuCollapsed(true));
        row.addView(close, new LinearLayout.LayoutParams(dp(44), -2));
        column.addView(row);
        column.addView(phoneRule());
        addGap(column, 6);
    }

    @Override public void onConfigurationChanged(android.content.res.Configuration configuration) {
        super.onConfigurationChanged(configuration);
        if (!phoneLayout || guide == null) return;

        boolean wasCollapsed = menuCollapsed;
        guide.removeCallbacks(directoryRefresh);
        guide.removeCallbacks(directoryRetry);
        if (programArtwork != null) programArtwork.removeCallbacks(previewClock);
        activityRoot.removeView(guide);
        liveRail = movieRail = auxiliaryPage = null;
        buildGuideUi(activityRoot);
        setMenuCollapsed(wasCollapsed);
        if (!creators.isEmpty()) {
            buildStudioRails();
            syncStudioScreen();
            if (movieShelfLoaded && currentStudio != null && currentStudio.cinemaSupported) renderMovies(currentStudio);
            showLoadedChannels();
        }
        if (testerConsented()) {
            guide.postDelayed(directoryRefresh, DIRECTORY_REFRESH_MS);
            if (directoryStale) guide.postDelayed(directoryRetry, DIRECTORY_RETRY_MS);
        }
        bufferPanel.getLayoutParams().width = playbackPanelWidth();
        bufferPanel.requestLayout();
    }

    private int playbackPanelWidth() {
        return phoneLayout
                ? Math.min(dp(360), dp(getResources().getConfiguration().screenWidthDp - 24)) : dp(360);
    }

    private String testerVersionLabel() { return alphaVersionLabel(BuildConfig.VERSION_NAME); }

    private static String alphaVersionLabel(String version) {
        return "Alpha V" + (version.endsWith(".0") ? version.substring(0, version.length() - 2) : version);
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
        if (BuildConfig.TESTER_BUILD && !testerConsented()) return;
        testerEvent("guide");
        if(directoryLoading)return;
        directoryLoading=true;
        if (creators.isEmpty()) status.setText("Scanning the public ChannelForge directory…");
        boolean restore = readInitialCache;
        readInitialCache = false;
        studioLoader.execute(() -> {
            List<Creator> discovered = new ArrayList<>();
            boolean authoritative = false;
            Exception failure = null;
            String saved = guideCache.read("directory");
            List<Creator> cached = new ArrayList<>();
            try {
                if (!saved.isEmpty()) cached.addAll(parseCreators(new JSONObject(saved)));
            } catch (JSONException invalid) {
                android.util.Log.w("ChannelForgeGuide", "Invalid directory snapshot");
            }
            if (restore && !cached.isEmpty()) {
                runOnUiThread(() -> {
                    if (!isFinishing() && !isDestroyed() && creators.isEmpty()) {
                        directoryStale = true;
                        creators.addAll(cached);
                        renderCreators();
                    }
                });
            }
            for (String address : new String[]{BuildConfig.DIRECTORY_URL, BuildConfig.DIRECTORY_FALLBACK_URL}) {
                try {
                    String body = get(address);
                    JSONObject doc = new JSONObject(body);
                    List<Creator> incoming = parseCreators(doc);
                    boolean primary = address.equals(BuildConfig.DIRECTORY_URL);
                    authoritative = primary && doc.optBoolean("registrations_complete", true);
                    if (primary && (authoritative || doc.getJSONArray("creators").length() > 0)) {
                        JSONObject snapshot = registrationSnapshot(doc, saved, authoritative);
                        guideCache.write("directory", snapshot.toString());
                        discovered.addAll(parseCreators(snapshot));
                        break;
                    }
                    if (!incoming.isEmpty()) {
                        discovered.addAll(incoming);
                        for (Creator previous : cached) {
                            boolean found = false;
                            for (Creator row : discovered) if (row.id.equals(previous.id)) { found = true; break; }
                            if (!found) discovered.add(previous);
                        }
                        break;
                    }
                } catch (Exception error) { failure = error; }
            }
            boolean stale = !authoritative && discovered.isEmpty() && !cached.isEmpty();
            if (stale) discovered.addAll(cached);
            String paired = getSharedPreferences(PREFS, MODE_PRIVATE).getString(PAIRED_CREATOR, "");
            if (!paired.isEmpty() && !hasCreator(discovered, paired + "/exports/channels.m3u")) {
                try { discovered.add(readPairedCreator(paired)); }
                catch (Exception error) { if (failure == null) failure = error; }
            }
            final Exception resultError = failure;
            runOnUiThread(() -> applyDirectory(discovered, resultError, stale));
        });
    }

    private JSONObject registrationSnapshot(JSONObject current, String saved, boolean authoritative) throws JSONException {
        long now = System.currentTimeMillis();
        JSONArray rows = current.getJSONArray("creators");
        Set<String> incoming = new HashSet<>();
        for (int i = 0; i < rows.length(); i++) {
            JSONObject row = rows.optJSONObject(i);
            if (row == null) continue;
            incoming.add(row.optString("id",row.optString("playlist_url")));
            row.put("observed_on_device", now);
        }
        if (!authoritative && !saved.isEmpty()) {
            JSONArray previous;
            try { previous = new JSONObject(saved).getJSONArray("creators"); }
            catch (JSONException invalid) { previous = new JSONArray(); }
            for (int i = 0; i < previous.length(); i++) {
                JSONObject row = previous.optJSONObject(i);
                if (row == null || now - row.optLong("observed_on_device") > 7L * 86400 * 1000) continue;
                if (incoming.add(row.optString("id",row.optString("playlist_url")))) {
                    if (!"offline".equals(row.optString("state"))) row.put("state", "reconnecting");
                    rows.put(row);
                }
            }
        }
        return current;
    }

    private List<Creator> parseCreators(JSONObject doc) throws JSONException {
        List<Creator> result = new ArrayList<>();
        JSONArray rows = doc.getJSONArray("creators");
        for (int i = 0; i < rows.length(); i++) {
            JSONObject row = rows.optJSONObject(i);
            if (row == null || "offline".equals(row.optString("state"))
                    || (row.optString("state").isEmpty() && !row.optBoolean("online", true))) continue;
            String playlist = row.optString("playlist_url");
            if (playlist.isEmpty() || hasCreator(result, playlist)) continue;
            Creator creator = new Creator(row.optString("name", "Creator"), row.optString("description"),
                    playlist, row.optString("vault_url"), row.optString("status_url"), row.optBoolean("cinema_supported"));
            creator.id = row.optString("id", playlist);
            creator.playbackOrigin = row.optString("playback_origin");
            creator.state = row.optString("state", row.optBoolean("online", true) ? "broadcasting" : "offline");
            creator.lastHeartbeat = row.optLong("last_heartbeat");
            creator.updatedAt = row.optLong("updated_at");
            result.add(creator);
        }
        return result;
    }

    private void applyDirectory(List<Creator> discovered, Exception failure, boolean stale) {
        directoryLoading = false;
        directoryChecked = System.currentTimeMillis();
        if (isFinishing() || isDestroyed()) return;
        guide.removeCallbacks(directoryRetry);
        directoryStale = stale;
        if (stale && guideVisible) guide.postDelayed(directoryRetry, DIRECTORY_RETRY_MS);
        if (discovered.isEmpty()) {
            creators.clear();
            channelsByStudio.clear();
            staleGuides.clear();
            unreachableStudios.clear();
            renderDirectoryRecovery(failure);
            return;
        }
        boolean changed = !sameCreators(discovered);
        for (Creator next : discovered) for (Creator old : creators) {
            if (!old.id.equals(next.id) || old.playlist.equals(next.playlist)) continue;
            if (old.playlist.equals(selectedPlaylist)) selectedPlaylist = next.playlist;
            if (old.playlist.equals(guideStudio)) guideStudio = next.playlist;
        }
        creators.clear();
        creators.addAll(discovered);
        Set<String> activeOrigins = new HashSet<>();
        for (Creator creator : creators) activeOrigins.add(creator.playlist);
        channelsByStudio.keySet().retainAll(activeOrigins);
        staleGuides.retainAll(activeOrigins);
        unreachableStudios.retainAll(activeOrigins);
        if (!activeOrigins.contains(guideStudio)) guideStudio = "";
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
            if (!old.state.equals(current.state) || !old.id.equals(current.id) || !old.playlist.equals(current.playlist) || !old.name.equals(current.name)
                    || !old.description.equals(current.description) || !old.vault.equals(current.vault)
                    || !old.channelStatus.equals(current.channelStatus)
                    || !old.playbackOrigin.equals(current.playbackOrigin)
                    || old.cinemaSupported != current.cinemaSupported) return false;
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
        return new Creator(host==null?"Paired Creator":host,"Paired directly with this Fire TV",
                base+"/exports/channels.m3u",base+"/api/public/vault",base+"/api/public/channels",
                state.optBoolean("cinema_supported"));
    }

    private void renderDirectoryRecovery(Exception error){
        channelRail.removeAllViews();
        liveRail = null;
        movieRail = null;
        currentStudio = null;
        auxiliaryPage = null;
        ++movieRequest;
        status.setText(error == null ? "NO PUBLIC STUDIOS ONLINE" : "PUBLIC DIRECTORY UNAVAILABLE  •  SELECT SCAN TO RETRY");
        TextView retry = focusButton("↻  SCAN PUBLIC DIRECTORY");
        retry.setOnClickListener(v -> loadDirectory());
        channelRail.addView(sectionTitle("LIVE TV GUIDE","#5DEEFF"));
        TextView detail=text(error==null?"No public studios are online. Scan for channels to check again."
                :"The public directory is unreachable. Scan for channels to retry.",15,false);
        detail.setTextColor(color("#C6D1E7"));
        channelRail.addView(detail);
        channelRail.addView(retry);
        retry.requestFocus();
    }

    private void showPairingDialog(){
        EditText input=new EditText(this);input.setSingleLine(true);input.setHint("https://your-studio.trycloudflare.com");input.setText(getSharedPreferences(PREFS,MODE_PRIVATE).getString(PAIRED_CREATOR,""));input.setSelectAllOnFocus(true);input.setPadding(30,18,30,18);
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("Pair ChannelForge Creator").setMessage("In Creator, open System → Publish worldwide and copy its HTTPS pairing URL.").setView(input).setNegativeButton("CANCEL",null).setNeutralButton("FORGET",(d,w)->{getSharedPreferences(PREFS,MODE_PRIVATE).edit().remove(PAIRED_CREATOR).apply();loadDirectory();}).setPositiveButton("PAIR",null).create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            String address = input.getText().toString().trim();
            if (address.isEmpty()) { input.setError("Enter the Studio HTTPS URL"); return; }
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(false);
            new Thread(() -> {
                try {
                    Creator creator = readPairedCreator(address);
                    runOnUiThread(() -> {
                        if (isFinishing() || isDestroyed() || !dialog.isShowing()) return;
                        getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(PAIRED_CREATOR,
                                address.replaceAll("/+$", "")).apply();
                        dialog.dismiss();
                        if (!hasCreator(creators, creator.playlist)) creators.add(creator);
                        unreachableStudios.remove(creator.playlist);
                        guideStudio = creator.playlist;
                        loadCreator(creator);
                    });
                } catch (Exception error) {
                    runOnUiThread(() -> {
                        if (!dialog.isShowing()) return;
                        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(true);
                        input.setError("Could not reach this Studio. Check its HTTPS URL and try again.");
                    });
                }
            }, "studio-pairing").start();
        }));
        dialog.show();
    }

    private TextView focusButton(String label) { TextView button=text(label,14,true);button.setTextColor(color("#DFFBFF"));button.setGravity(Gravity.CENTER_VERTICAL);button.setMinHeight(dp(38));button.setFocusable(true);button.setBackground(surface(new int[]{color("#151A3D"),color("#101127")},color("#1BDFF5"),1,7));button.setOnFocusChangeListener((v, focused)->{v.setBackground(surface(focused?new int[]{color("#6A145C"),color("#231454")}:new int[]{color("#151A3D"),color("#101127")},focused?color("#FF42C5"):color("#1BDFF5"),focused?2:1,7));v.setScaleX(focused?1.018f:1f);v.setScaleY(focused?1.018f:1f);v.setTranslationZ(focused?dp(5):0);});return button;}

    private void renderCreators() {
        Creator selected = preferredCreator();
        loadCreator(cinemaScreen && !selected.cinemaSupported ? firstCinemaStudio(selected) : selected);
    }

    private Creator preferredCreator() {
        List<Creator> visible = reachableCreators();
        for (Creator creator : visible) if (creator.playlist.equals(selectedPlaylist)) return creator;
        return visible.isEmpty() ? creators.get(0) : visible.get(0);
    }

    private List<Creator> reachableCreators() {
        List<Creator> visible = new ArrayList<>();
        for (Creator creator : creators) {
            if (!unreachableStudios.contains(creator.playlist)) visible.add(creator);
        }
        return visible;
    }

    private void buildStudioRails() {
        channelRail.removeAllViews();
        auxiliaryPage = null;
        liveRail = new LinearLayout(this);
        liveRail.setOrientation(LinearLayout.VERTICAL);
        movieRail = new LinearLayout(this);
        movieRail.setOrientation(LinearLayout.VERTICAL);
        channelRail.addView(liveRail);
        channelRail.addView(movieRail);
        liveRail.addView(text("Finding live channels…", 14, false));
        movieRail.addView(text("Loading Rewind Cinema…", 14, false));
    }

    private void loadCreator(Creator creator) {
        currentStudio = creator;
        boolean changed = !creator.playlist.equals(selectedPlaylist) || liveRail == null || movieRail == null;
        selectedPlaylist = creator.playlist;
        if (changed) {
            ++movieRequest;
            movieGenre = movieRating = "";
            moviePageLoading = movieShelfLoaded = false;
            nextMovieOffset = -1;
            movieShelf = null;
            moreMovies = null;
            vaultItems.clear();
            channelIndex = 0;
            buildStudioRails();
        }
        syncStudioScreen();
        for (Creator studio : creators) loadStudioGuide(studio);
        showLoadedChannels();
        if (cinemaScreen && creator.cinemaSupported && !movieShelfLoaded && !moviePageLoading)
            loadMoviePage(creator, 0);
    }

    private void loadStudioGuide(Creator creator) {
        if (!loadingGuides.add(creator.playlist)) return;
        studioLoader.execute(() -> {
            List<Channel> loaded = null;
            String address = Uri.parse(creator.channelStatus).buildUpon().appendQueryParameter("guide", "extended").build().toString();
            boolean degraded = false;
            try {
                if (!creator.channelStatus.isEmpty()) {
                    loaded = parseOnlineChannels(get(address), address);
                }
            } catch (Exception unavailable) {
                android.util.Log.w("ChannelForgeGuide", "Studio EPG request failed: " + unavailable.getClass().getSimpleName());
            }
            if (loaded == null) {
                degraded = true;
                try {
                    String playlist = get(creator.playlist);
                    if (playlist.trim().startsWith("#EXTM3U")) loaded = parseM3u(playlist, creator.playlist);
                } catch (Exception unavailable) {
                    android.util.Log.w("ChannelForgeGuide", "Playlist fallback unavailable");
                }
            }
            final List<Channel> result = loaded;
            final boolean stale = degraded;
            runOnUiThread(() -> {
                loadingGuides.remove(creator.playlist);
                if (isFinishing() || isDestroyed() || !hasCreator(creators, creator.playlist)) return;
                boolean changed = result == null ? unreachableStudios.add(creator.playlist)
                        : unreachableStudios.remove(creator.playlist);
                if (changed) {
                    if (guideStudio.equals(creator.playlist) && result == null) guideStudio = "";
                    if (cinemaScreen && currentStudio == creator && result == null) {
                        Creator online = firstCinemaStudio(creator);
                        if (online != creator) { loadCreator(online); return; }
                    }
                }
                applyGuide(creator, result, stale);
            });
        });
    }

    private void applyGuide(Creator creator, List<Channel> rows, boolean stale) {
        if (isFinishing() || isDestroyed() || !hasCreator(creators, creator.playlist)) return;
        if (stale && rows != null) staleGuides.add(creator.playlist);
        else staleGuides.remove(creator.playlist);
        if (rows == null) {
            channelsByStudio.remove(creator.playlist);
        } else {
            List<Channel> prior = stale ? channelsByStudio.get(creator.playlist) : null;
            for (Channel channel : rows) {
                channel.studio = creator.name;
                channel.studioId = creator.id;
                if (prior == null) continue;
                for (Channel previous : prior) {
                    if ((!channel.id.isEmpty() && channel.id.equals(previous.id)) || channel.url.equals(previous.url)) {
                        channel.programs = previous.programs;
                        channel.airUntil = previous.airUntil;
                        if (channel.url.equals(previous.url)) {
                            channel.continuousUrl = previous.continuousUrl;
                            channel.fireTvUrl = previous.fireTvUrl;
                        }
                        break;
                    }
                }
            }
            channelsByStudio.put(creator.playlist, rows);
        }
        showLoadedChannels();
    }

    private void showLoadedChannels() {
        if (!guideVisible || liveRail == null) return;
        channels.clear();
        Map<String, Channel> unique = new java.util.LinkedHashMap<>();
        Map<String, Creator> origin = new HashMap<>();
        long now = System.currentTimeMillis();
        for (Creator studio : reachableCreators()) {
            if (activePage != Page.LIVE || guideStudio.isEmpty() || studio.playlist.equals(guideStudio)) {
                List<Channel> loaded = channelsByStudio.get(studio.playlist);
                if (loaded == null) continue;
                String name = studio.name.trim().toLowerCase(java.util.Locale.ROOT);
                boolean fresh = !staleGuides.contains(studio.playlist);
                for (Channel channel : loaded) {
                    if (channel.airUntil > 0 && channel.airUntil <= now) continue;
                    String key = !name.isEmpty() && channel.id.matches("[a-fA-F0-9]{16}") ? name + "/" + channel.id : channel.url;
                    Creator previous = origin.get(key);
                    boolean previousFresh = previous != null && !staleGuides.contains(previous.playlist);
                    if (previous == null || (fresh != previousFresh ? fresh : studio.updatedAt > previous.updatedAt)) {
                        origin.put(key, studio);
                        unique.put(key, channel);
                    }
                }
            }
        }
        Set<String> urls = new HashSet<>();
        for (Channel channel : unique.values()) if (urls.add(channel.url)) channels.add(channel);
        channelIndex = Math.min(channelIndex, Math.max(0, channels.size() - 1));
        String name = currentStudio == null ? "Worldwide" : currentStudio.name;
        if (activePage == Page.LIVE) {
            name = "All studios";
            for (Creator studio : creators) {
                if (studio.playlist.equals(guideStudio)) { name = studio.name; break; }
            }
        }
        renderChannels(name);
        if (activePage == Page.HOME) renderHomeContent();
        if (activePage == Page.FAVORITES) syncStudioScreen();
    }

    private TextView navigationButton(String label) {
        TextView button = focusButton(label);
        button.setTextSize(phonePortrait ? 14 : 11);
        button.setMinHeight(dp(phonePortrait ? 43 : 30));
        button.setPadding(dp(9), dp(3), dp(8), dp(3));
        button.setMaxLines(1);
        button.setEllipsize(android.text.TextUtils.TruncateAt.END);
        if (tvTheme) {
            button.setTypeface(Typeface.create("sans-serif-condensed", Typeface.BOLD));
            button.setMinHeight(dp(25));
            button.setTextSize(13);
        }
        return button;
    }

    private void addNavigation(String icon, String label, Page page) {
        TextView button = navigationButton(icon + "  " + label);
        button.setTag(new String[]{icon, label});
        button.setContentDescription(label);
        button.setOnClickListener(v -> {
            playMenuSound();
            openPage(page);
        });
        button.setOnFocusChangeListener((view, focused) -> {
            if (focused) playMenuSound();
            paintNavigation(button, page);
        });
        paintNavigation(button, page);
        navigationRail.addView(button);
        navButtons.put(page, button);
    }

    private void paintNavigation(TextView button, Page page) {
        boolean selected = activePage == page;
        if (tvTheme) {
            button.setBackground(selected || button.isFocused() ? tvFrame(true, 8) : null);
            button.setTextColor(color("#F4F7FF"));
            return;
        }
        if (neonTheme() && (selected || button.isFocused())) {
            button.setBackground(phonePanel(!phonePortrait && selected, 7));
        } else if (selected || button.isFocused()) {
            button.setBackground(surface(new int[]{color("#251849"), color("#17102C")},
                    color(button.isFocused() ? "#FF77D9" : "#D242AA"), 1, 7));
        } else button.setBackgroundColor(Color.TRANSPARENT);
        button.setTextColor(color(selected ? "#FFFFFF" : "#B7C8E5"));
    }

    private void toggleMenu() {
        playMenuSound();
        setMenuCollapsed(!menuCollapsed);
        if (!phonePortrait && activePage == Page.LIVE) showLoadedChannels();
    }

    private void playMenuSound() {
        long now = SystemClock.elapsedRealtime();
        if (!menuSoundReady || now - lastMenuSound < 65
                || Settings.System.getInt(getContentResolver(), Settings.System.SOUND_EFFECTS_ENABLED, 1) == 0) return;
        lastMenuSound = now;
        menuSounds.play(menuTick, .32f, .32f, 0, 0, 1f);
    }

    private void setMenuCollapsed(boolean collapsed) {
        boolean wasCollapsed = menuCollapsed;
        menuCollapsed = collapsed;
        menuPanel.getLayoutParams().width = !phonePortrait && menuCollapsed ? dp(phoneLayout ? 49 : 44) : navigationWidth;
        menuPanel.setVisibility(phonePortrait && collapsed ? View.GONE : View.VISIBLE);
        if (phonePortrait) {
            phoneMenuShade.setVisibility(collapsed ? View.GONE : View.VISIBLE);
            phoneContent.setImportantForAccessibility(collapsed ? View.IMPORTANT_FOR_ACCESSIBILITY_AUTO
                    : View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
            phoneHeader.setVisibility(collapsed ? View.VISIBLE : View.GONE);
            phoneHeaderLine.setVisibility(collapsed ? View.VISIBLE : View.GONE);
            status.setVisibility(View.GONE);
            menuPanel.animate().cancel();
            if (!collapsed && wasCollapsed && motionEnabled()) {
                menuPanel.setTranslationX(-navigationWidth);
                menuPanel.animate().translationX(0).setDuration(210).start();
            } else menuPanel.setTranslationX(0);
        }
        menuPanel.requestLayout();
        for (TextView button : navButtons.values()) {
            String[] parts = (String[]) button.getTag();
            button.setText(menuCollapsed ? parts[0] : parts[0] + "  " + parts[1]);
            if (phoneLayout && !phonePortrait) {
                button.setGravity(collapsed ? Gravity.CENTER : Gravity.CENTER_VERTICAL);
                button.setTextSize(collapsed ? 21 : 12);
                button.setMinHeight(dp(collapsed ? 40 : 35));
            }
        }
    }

    private void openCinemaScreen(boolean cinema) {
        openPage(cinema ? Page.CINEMA : Page.LIVE);
    }

    private void openPage(Page page) {
        if (phonePortrait && !menuCollapsed) setMenuCollapsed(true);
        if (!BuildConfig.TESTER_BUILD && page == Page.LIVE && activePage != Page.LIVE) setMenuCollapsed(true);
        activePage = page;
        cinemaScreen = page == Page.CINEMA;
        if (cinemaScreen && currentStudio != null && !currentStudio.cinemaSupported) {
            Creator supported = firstCinemaStudio(currentStudio);
            if (supported != currentStudio) { loadCreator(supported); return; }
        }
        syncStudioScreen();
        if (page == Page.LIVE || page == Page.FAVORITES || page == Page.HOME) showLoadedChannels();
        if (cinemaScreen && currentStudio != null && currentStudio.cinemaSupported && !movieShelfLoaded && !moviePageLoading)
            loadMoviePage(currentStudio, 0);
    }

    private Creator firstCinemaStudio(Creator fallback) {
        for (Creator item : reachableCreators()) if (item.cinemaSupported) return item;
        return fallback;
    }

    private void syncStudioScreen() {
        if (channelRail == null) return;
        for (Map.Entry<Page, TextView> entry : navButtons.entrySet()) paintNavigation(entry.getValue(), entry.getKey());
        screenSwitcher.setText(cinemaScreen ? "LIVE TV GUIDE" : "REWIND CINEMA");
        if (liveRail != null) liveRail.setVisibility(activePage == Page.LIVE ? View.VISIBLE : View.GONE);
        boolean showMovies = cinemaScreen && currentStudio != null && currentStudio.cinemaSupported;
        if (movieRail != null) movieRail.setVisibility(showMovies ? View.VISIBLE : View.GONE);
        if (auxiliaryPage != null && renderedAuxiliary == activePage
                && (activePage == Page.HOME || activePage == Page.SYSTEM)) return;
        if (auxiliaryPage != null) channelRail.removeView(auxiliaryPage);
        auxiliaryPage = null;
        if ((activePage == Page.LIVE && liveRail != null) || (showMovies && movieRail != null)) return;
        auxiliaryPage = new LinearLayout(this);
        renderedAuxiliary = activePage;
        auxiliaryPage.setOrientation(LinearLayout.VERTICAL);
        channelRail.addView(auxiliaryPage);
        if (activePage == Page.HOME) {
            buildHomePage();
        } else if (activePage == Page.FAVORITES) {
            auxiliaryPage.addView(sectionTitle("FAVORITE CHANNELS", "#63EFFF"));
            boolean found = false;
            for (int i = 0; i < channels.size(); i++) {
                int index = i;
                Channel channel = channels.get(index);
                if (!isFavorite(channel)) continue;
                found = true;
                TextView row = focusButton("★  CH " + displayNumber(channel, index) + "  " + channel.name);
                row.setOnClickListener(v -> play(index));
                auxiliaryPage.addView(row);
            }
            if (!found) auxiliaryPage.addView(text("Use the star in the guide to save a channel.", 13, false));
        } else if (activePage == Page.CONTINUE) {
            auxiliaryPage.addView(sectionTitle("CONTINUE WATCHING", "#63EFFF"));
            showContinueWatching();
        } else if (activePage == Page.SYSTEM) {
            buildSystemPage();
        } else {
            auxiliaryPage.addView(text(cinemaScreen ? "No compatible movie studios are online."
                    : "Waiting for a public studio. Select Scan for Channels to retry.", 14, false));
            TextView retry = focusButton("↻ SCAN FOR CHANNELS");
            retry.setOnClickListener(v -> loadDirectory());
            auxiliaryPage.addView(retry);
        }
    }

    private void openSystemUpdates() {
        if (BuildConfig.TESTER_BUILD && testerConsented()) checkUpdates(true);
        else selectUpdate();
    }

    private void openSystemDiagnostics() {
        if (BuildConfig.TESTER_BUILD) showTesterTools();
        else showDiagnostics();
    }

    private void buildSystemPage() {
        LinearLayout options = panel();
        if (neonTheme()) options.setBackground(phonePanel(false, 11));
        options.addView(sectionTitle("SYSTEM", "#53EFFF"));
        options.addView(homeTile("↻", "Check for Update", "Find a newer version of ChannelForge TV", this::openSystemUpdates));
        addGap(options, 7);
        options.addView(homeTile("◇", BuildConfig.TESTER_BUILD ? "Developer" : "Diagnostics",
                "View playback and connection details", this::openSystemDiagnostics));
        if (BuildConfig.TESTER_BUILD) {
            addGap(options, 7);
            options.addView(homeTile("＋", "Pair a Studio", "Connect to a Studio by its address", this::showPairingDialog));
        }
        auxiliaryPage.addView(options);
    }

    private void buildHomePage() {
        homeLiveContent = panel();
        homeLiveKey = null;
        auxiliaryPage.addView(homeLiveContent);
        homeCinemaContent = panel();
        auxiliaryPage.addView(homeCinemaContent);
        homePersonalContent = panel();
        homePersonalKey = null;
        auxiliaryPage.addView(homePersonalContent);
        renderHomeContent();
    }

    private void renderHomeContent() {
        if (homeLiveContent == null || homeCinemaContent == null) return;
        long now = System.currentTimeMillis();
        List<Channel> shelf = channels.subList(0, Math.min(4, channels.size()));
        StringBuilder signature = new StringBuilder();
        for (Channel channel : shelf) {
            JSONObject current = programAt(channel, now);
            signature.append(channel.url).append('\n').append(current == null ? "" : current.optString("start")).append('\n');
        }
        if (!signature.toString().equals(homeLiveKey)) {
            homeLiveKey = signature.toString();
            homeLiveContent.removeAllViews();
            if (shelf.isEmpty()) {
                homeLiveContent.addView(homeSpotlight(null, null, now));
            } else {
                Channel featured = shelf.get(0);
                for (Channel channel : shelf) if (programAt(channel, now) != null) { featured = channel; break; }
                Channel selected = featured;
                JSONObject current = programAt(selected, now);
                homeLiveContent.addView(homeSpotlight(selected, current, now));
                homeLiveContent.addView(sectionTitle("ON NOW", "#5AEFFF"));
                LinearLayout cards = new LinearLayout(this);
                if (phonePortrait) cards.setOrientation(LinearLayout.VERTICAL);
                for (Channel channel : shelf) cards.addView(homeProgramCard(channel, programAt(channel, now), now));
                if (phonePortrait) homeLiveContent.addView(cards);
                else addHorizontalStrip(homeLiveContent, cards);
            }
        }
        Creator cinema = firstCinemaStudio(null);
        String address = cinema == null ? "" : cinema.vault;
        if (!address.equals(homeCinemaAddress)) {
            homeCinemaAddress = address;
            homeCinemaMovies = Collections.emptyList();
            paintHomeCinema();
            if (!address.isEmpty()) movieCatalogLoader.execute(() -> {
                try {
                    String page = Uri.parse(address).buildUpon().appendQueryParameter("limit", "4").build().toString();
                    List<VaultItem> movies = parseVault(new JSONObject(get(page)));
                    for (VaultItem movie : movies) movie.studioId = cinema.id;
                    runOnUiThread(() -> {
                        if (isFinishing() || isDestroyed() || !address.equals(homeCinemaAddress)) return;
                        homeCinemaMovies = movies.subList(0, Math.min(4, movies.size()));
                        if (activePage == Page.HOME) paintHomeCinema();
                    });
                } catch (Exception ignored) { /* Keep the Browse action available. */ }
            });
        }
        if (homeCinemaContent.getChildCount() == 0) paintHomeCinema();
        renderHomePersonal();
    }

    private void renderHomePersonal() {
        if (homePersonalContent == null) return;
        VaultItem saved = null;
        long resumeAt = 0;
        JSONArray history = movieHistory();
        for (int index = 0; index < history.length(); index++) {
            JSONObject entry = history.optJSONObject(index);
            if (entry != null && !entry.optString("url").isEmpty() && entry.optLong("position") > 0) {
                saved = movieFromHistory(entry);
                resumeAt = entry.optLong("position");
                break;
            }
        }
        Channel favorite = null;
        for (Channel channel : channels) if (isFavorite(channel)) { favorite = channel; break; }
        String key = (saved == null ? "" : saved.url + "@" + resumeAt) + "\n" + (favorite == null ? "" : favorite.url);
        if (key.equals(homePersonalKey)) return;
        homePersonalKey = key;
        homePersonalContent.removeAllViews();
        homePersonalContent.setVisibility(saved == null && favorite == null ? View.GONE : View.VISIBLE);
        if (saved != null) {
            VaultItem movie = saved;
            long position = resumeAt;
            homePersonalContent.addView(homeTile("▷", movie.title, "Continue at " + elapsedTime(position), () -> playMovie(movie, position)));
        }
        if (favorite != null) {
            Channel channel = favorite;
            homePersonalContent.addView(homeTile("★", channel.name, "Watch favorite live", () -> play(channel)));
        }
    }

    private View homeSpotlight(Channel channel, JSONObject program, long now) {
        FrameLayout hero = new FrameLayout(this);
        hero.setBackground(phonePanel(false, 9));
        hero.setClipToOutline(true);
        int minimumHeight = phonePortrait ? 195 : 180;
        hero.setMinimumHeight(dp(minimumHeight));
        String art = channel == null ? "" : channelArtwork(channel, program);
        if (!art.isEmpty()) {
            ImageView artwork = new ImageView(this);
            artwork.setScaleType(ImageView.ScaleType.CENTER_CROP);
            artwork.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
            int width = getResources().getConfiguration().screenWidthDp;
            hero.addView(artwork, new FrameLayout.LayoutParams(phonePortrait ? -1 : dp(width / 2), dp(minimumHeight), Gravity.END | Gravity.CENTER_VERTICAL));
            posterLoader.execute(() -> loadPoster(art, artwork));
        }
        View shade = new View(this);
        shade.setBackground(new GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT,
                new int[]{color("#FF080E1C"), color(phonePortrait ? "#DB080E1C" : "#D4080E1C"), color("#18080E1C")}));
        hero.addView(shade, new FrameLayout.LayoutParams(-1, -1));
        LinearLayout copy = panel();
        copy.setGravity(Gravity.CENTER_VERTICAL);
        copy.setMinimumHeight(dp(minimumHeight));
        copy.setPadding(dp(13), dp(9), dp(13), dp(9));
        if (channel != null) copy.addView(sectionTitle("LIVE NOW", "#F352E8"));
        TextView title = text(program != null ? program.optString("title", channel.name) : channel == null ? "Live TV" : channel.name,
                phonePortrait ? 24 : 32, true);
        title.setTypeface(Typeface.create("sans-serif-condensed", Typeface.BOLD_ITALIC));
        title.setMaxLines(2);
        title.setEllipsize(TextUtils.TruncateAt.END);
        copy.addView(title);
        copy.addView(text(channel == null ? "No channels are online right now." : channel.name + "  •  CH " + channel.number, 14, true));
        addHomeProgress(copy, program, now);
        LinearLayout actions = new LinearLayout(this);
        if (channel != null) guideControl(actions, "▶ WATCH LIVE", "home-watch", () -> play(channel), null);
        guideControl(actions, "OPEN GUIDE", "home-guide", () -> openPage(Page.LIVE), null);
        copy.addView(actions);
        hero.addView(copy, new FrameLayout.LayoutParams(-1, -2, Gravity.CENTER_VERTICAL));
        return hero;
    }

    private View homeProgramCard(Channel channel, JSONObject program, long now) {
        LinearLayout card = new LinearLayout(this);
        card.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(phonePortrait ? -1 : dp(235), dp(phonePortrait ? 64 : 70));
        if (phonePortrait) layout.bottomMargin = dp(6);
        else layout.setMarginEnd(dp(8));
        card.setLayoutParams(layout);
        card.setFocusable(true);
        card.setContentDescription("Watch live: " + channel.name);
        card.setBackground(phonePanel(false, 8));
        card.setClipToOutline(true);
        card.setOnFocusChangeListener((view, focused) -> card.setBackground(phonePanel(focused, 8)));
        card.setOnClickListener(view -> play(channel));
        String art = channelArtwork(channel, program);
        ImageView artwork = new ImageView(this);
        artwork.setScaleType(ImageView.ScaleType.CENTER_CROP);
        artwork.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        card.addView(artwork, new LinearLayout.LayoutParams(dp(81), -1));
        if (!art.isEmpty()) posterLoader.execute(() -> loadPoster(art, artwork));
        LinearLayout copy = panel();
        copy.setPadding(dp(6), 0, dp(5), 0);
        TextView station = text(channel.name, 13, true);
        station.setMaxLines(1);
        station.setEllipsize(TextUtils.TruncateAt.END);
        copy.addView(station);
        TextView label = text(program == null ? "CH " + channel.number : program.optString("title", channel.name), 12, false);
        label.setMaxLines(1);
        label.setEllipsize(TextUtils.TruncateAt.END);
        copy.addView(label);
        addHomeProgress(copy, program, now);
        card.addView(copy, new LinearLayout.LayoutParams(0, -2, 1));
        return card;
    }

    private void addHomeProgress(LinearLayout copy, JSONObject program, long now) {
        if (program == null) return;
        long start = guideTimeMillis(program, "start");
        long duration = guideTimeMillis(program, "stop") - start;
        if (duration <= 0) return;
        ProgressBar progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(1000);
        progress.setProgress((int) Math.min(1000, Math.max(0, 1000 * (now - start) / duration)));
        progress.setContentDescription("Program progress");
        progress.setProgressTintList(android.content.res.ColorStateList.valueOf(color("#F23BD9")));
        LinearLayout.LayoutParams size = new LinearLayout.LayoutParams(-1, dp(6));
        size.setMargins(dp(4), dp(4), dp(6), dp(7));
        copy.addView(progress, size);
    }

    private String channelArtwork(Channel channel, JSONObject program) {
        if (program != null) {
            for (String key : new String[]{"backdrop_url", "series_poster_url", "poster_url"}) {
                String address = program.optString(key);
                if (!address.isEmpty()) return address;
            }
        }
        return channel.logo;
    }

    private void paintHomeCinema() {
        homeCinemaContent.removeAllViews();
        homeCinemaContent.addView(sectionTitle("FROM REWIND CINEMA", "#F0A7FF"));
        LinearLayout row = new LinearLayout(this);
        if (phonePortrait) row.setOrientation(LinearLayout.VERTICAL);
        for (VaultItem item : homeCinemaMovies) {
            FrameLayout tape = new FrameLayout(this);
            LinearLayout.LayoutParams size = new LinearLayout.LayoutParams(phonePortrait ? -1 : dp(237), dp(61));
            if (phonePortrait) size.bottomMargin = dp(6);
            else size.setMarginEnd(dp(8));
            tape.setFocusable(true);
            tape.setContentDescription("Select " + item.title);
            tape.setBackground(phonePanel(false, 8));
            tape.setClipToOutline(true);
            ImageView shell = new ImageView(this);
            shell.setScaleType(ImageView.ScaleType.FIT_XY);
            shell.setImageResource(R.drawable.cinema_spine);
            shell.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
            tape.addView(shell, new FrameLayout.LayoutParams(-1, -1));
            TextView title = text(item.title, 15, true);
            title.setGravity(Gravity.CENTER_VERTICAL);
            title.setTextColor(color("#211621"));
            title.setBackgroundColor(color("#F0D2A7"));
            title.setMaxLines(1);
            title.setEllipsize(TextUtils.TruncateAt.END);
            FrameLayout.LayoutParams label = new FrameLayout.LayoutParams(-1, dp(32), Gravity.CENTER_VERTICAL);
            label.setMargins(dp(16), dp(8), dp(18), 0);
            tape.addView(title, label);
            tape.setOnFocusChangeListener((view, focused) -> title.setBackgroundColor(color(focused ? "#F1A9FD" : "#F0D2A7")));
            tape.setOnClickListener(view -> showPhoneMovieDetails(item));
            row.addView(tape, size);
        }
        guideControl(row, "BROWSE MOVIES", "home-cinema", () -> openPage(Page.CINEMA), null);
        if (phonePortrait) homeCinemaContent.addView(row);
        else addHorizontalStrip(homeCinemaContent, row);
    }

    private View navigationGlyph(Page page, boolean purple) {
        View icon = new View(this) {
            private final android.graphics.Paint ink = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
            private final android.graphics.Path path = new android.graphics.Path();

            @Override protected void onDraw(android.graphics.Canvas canvas) {
                float unit = Math.min(getWidth(), getHeight()) / 32f;
                canvas.save();
                canvas.translate((getWidth() - unit * 32) / 2, (getHeight() - unit * 32) / 2);
                canvas.scale(unit, unit);
                ink.setColor(color(purple ? "#F085FF" : "#4BECFF"));
                ink.setStyle(android.graphics.Paint.Style.STROKE);
                ink.setStrokeJoin(android.graphics.Paint.Join.ROUND);
                ink.setStrokeCap(android.graphics.Paint.Cap.ROUND);
                ink.setStrokeWidth(2.1f);
                if (page == Page.CINEMA) {
                    canvas.drawCircle(15, 15, 11, ink);
                    for (int i = 0; i < 5; i++) {
                        canvas.save();
                        canvas.rotate(72 * i, 15, 15);
                        canvas.drawCircle(15, 9, 2f, ink);
                        canvas.restore();
                    }
                    canvas.drawLine(15, 26, 29, 26, ink);
                } else if (page == Page.FAVORITES) {
                    path.reset();
                    for (int i = 0; i < 10; i++) {
                        double angle = i * Math.PI / 5 - Math.PI / 2;
                        float radius = i % 2 == 0 ? 12 : 5.5f;
                        float x = 16 + radius * (float) Math.cos(angle), y = 16 + radius * (float) Math.sin(angle);
                        if (i == 0) path.moveTo(x, y);
                        else path.lineTo(x, y);
                    }
                    path.close();
                    canvas.drawPath(path, ink);
                } else {
                    if (page == Page.LIVE) {
                        canvas.drawRoundRect(3, 10, 29, 29, 3, 3, ink);
                        canvas.drawLine(16, 10, 11, 4, ink);
                        canvas.drawLine(16, 10, 22, 4, ink);
                    }
                    path.reset();
                    if (page == Page.LIVE) {
                        path.moveTo(13, 15); path.lineTo(21, 19.5f); path.lineTo(13, 24);
                    } else {
                        path.moveTo(9, 5); path.lineTo(26, 16); path.lineTo(9, 27);
                    }
                    path.close();
                    canvas.drawPath(path, ink);
                }
                canvas.restore();
            }
        };
        icon.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        return icon;
    }

    private View homeTile(String symbol, String title, String subtitle, Runnable action) {
        LinearLayout tile = new LinearLayout(this);
        tile.setGravity(Gravity.CENTER_VERTICAL);
        tile.setPadding(dp(6), dp(5), dp(5), dp(5));
        tile.setFocusable(true);
        tile.setContentDescription(title);
        applyTileFocus(tile, false);
        tile.setOnFocusChangeListener((view, focused) -> applyTileFocus(view, focused));
        TextView icon = text(symbol, 25, true);
        icon.setTextColor(color("#48ECFF"));
        tile.addView(icon);
        LinearLayout labels = new LinearLayout(this);
        labels.setOrientation(LinearLayout.VERTICAL);
        labels.addView(text(title, 16, true));
        labels.addView(text(subtitle, 11, false));
        tile.addView(labels, new LinearLayout.LayoutParams(0, -2, 1));
        tile.setOnClickListener(view -> action.run());
        return tile;
    }

    private void applyTileFocus(View tile, boolean focused) {
        tile.setBackground(surface(new int[]{color(focused ? "#3D1C62" : "#0D324D"), color("#16132D")},
                color(focused ? "#FA6FE7" : "#42DDF5"), focused ? 2 : 1, 9));
    }

    private void showContinueWatching() {
        JSONArray history = movieHistory();
        int added = 0;
        for (int index = 0; index < history.length(); index++) {
            JSONObject entry = history.optJSONObject(index);
            if (entry == null) continue;
            VaultItem movie = movieFromHistory(entry);
            long position = entry.optLong("position");
            if (!movie.url.isEmpty() && position > 0) {
                if (BuildConfig.TESTER_BUILD) {
                    auxiliaryPage.addView(vaultBox(movie, "Resume at " + elapsedTime(position),
                            view -> showResumeChoices(movie, position)));
                } else {
                    TextView resume = focusButton(movie.title + "  •  RESUME AT " + elapsedTime(position));
                    resume.setOnClickListener(v -> playMovie(movie, position));
                    auxiliaryPage.addView(resume);
                }
                added++;
            }
        }
        if (added == 0) auxiliaryPage.addView(text("No unfinished movies yet.", 13, false));
    }

    private VaultItem movieFromHistory(JSONObject entry) {
        VaultItem movie = new VaultItem(entry.optString("title"), entry.optString("year"), "",
                entry.optString("poster"), entry.optString("fullPoster"), entry.optString("url"));
        movie.id = entry.optString("id");
        movie.studioId = entry.optString("studioId");
        return movie;
    }

    private void showResumeChoices(VaultItem movie, long position) {
        new AlertDialog.Builder(this).setTitle(movie.title).setMessage("Resume at " + elapsedTime(position))
                .setPositiveButton("Continue watching", (box, button) -> playMovie(movie, position))
                .setNeutralButton("Remove from watch list", (box, button) -> {
                    saveMovieHistory(movie, null);
                    auxiliaryPage.removeAllViews();
                    auxiliaryPage.addView(sectionTitle("CONTINUE WATCHING", "#63EFFF"));
                    showContinueWatching();
                    if (auxiliaryPage.getChildCount() > 1) auxiliaryPage.getChildAt(1).requestFocus();
                }).setNegativeButton("Cancel", null).show();
    }

    private JSONArray movieHistory() {
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        try {
            String saved = prefs.getString("movie_history", "");
            if (!saved.isEmpty()) return new JSONArray(saved);
            String legacy = prefs.getString("continue_movie", "");
            if (!legacy.isEmpty()) return new JSONArray().put(new JSONObject(legacy));
        } catch (JSONException ignored) { }
        return new JSONArray();
    }

    private void rememberMovie() {
        if (activeMovie == null) return;
        long position = firstFrameMs < 0 ? resumePosition : player.getCurrentPosition();
        if (!movieFinished && position <= 0) return;
        try {
            JSONObject entry = null;
            if (!movieFinished) {
                entry = new JSONObject();
                entry.put("title", activeMovie.title).put("id", activeMovie.id).put("year", activeMovie.year)
                        .put("studioId", activeMovie.studioId)
                        .put("poster", activeMovie.poster).put("fullPoster", activeMovie.fullPoster)
                        .put("url", activeMovie.url).put("position", position);
            }
            saveMovieHistory(activeMovie, entry);
        } catch (JSONException ignored) { }
    }

    private void saveMovieHistory(VaultItem movie, JSONObject entry) {
        JSONArray updated = new JSONArray();
        if (entry != null) updated.put(entry);
        JSONArray previous = movieHistory();
        for (int index = 0; index < previous.length() && updated.length() < 20; index++) {
            JSONObject saved = previous.optJSONObject(index);
            if (saved == null || movie.url.equals(saved.optString("url"))) continue;
            if (!movie.id.isEmpty() && !movie.studioId.isEmpty()
                    && movie.id.equals(saved.optString("id")) && movie.studioId.equals(saved.optString("studioId"))) continue;
            updated.put(saved);
        }
        getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString("movie_history", updated.toString())
                .remove("continue_movie").apply();
    }

    private String favoriteKey(Channel channel) {
        return channel.id.isEmpty() ? channel.studio + Uri.parse(channel.url).getPath()
                : channel.studioId + "/" + channel.id;
    }

    private boolean isFavorite(Channel channel) {
        Set<String> saved = getSharedPreferences(PREFS, MODE_PRIVATE).getStringSet("favorite_channels", Collections.emptySet());
        return saved.contains(favoriteKey(channel)) || saved.contains(channel.studio + Uri.parse(channel.url).getPath());
    }

    private void toggleFavorite(Channel channel, TextView button) {
        SharedPreferences preferences = getSharedPreferences(PREFS, MODE_PRIVATE);
        Set<String> saved = new HashSet<>(preferences.getStringSet("favorite_channels", Collections.emptySet()));
        String key = favoriteKey(channel);
        boolean removed = saved.remove(key);
        removed |= saved.remove(channel.studio + Uri.parse(channel.url).getPath());
        boolean added = !removed;
        if (added) saved.add(key);
        preferences.edit().putStringSet("favorite_channels", saved).apply();
        button.setText(added ? "★" : "☆");
        button.setContentDescription((added ? "Remove from favorites: " : "Add to favorites: ") + channel.name);
    }

    private void loadMoviePage(Creator creator, int offset) {
        if (moviePageLoading) return;
        moviePageLoading = true;
        int movieLoad = ++movieRequest;
        final String decade = movieDecade, query = movieSearch, genre = movieGenre, rating = movieRating;
        if (moreMovies != null) moreMovies.setText("LOADING MOVIES…");
        movieCatalogLoader.execute(() -> {
            try {
                JSONObject page = new JSONObject();
                if (!creator.vault.isEmpty()) {
                    String address = Uri.parse(creator.vault).buildUpon()
                            .appendQueryParameter("sort", "title").appendQueryParameter("q", query)
                            .appendQueryParameter("decade", decade).appendQueryParameter("genre", genre)
                            .appendQueryParameter("rating", rating)
                            .appendQueryParameter("limit", String.valueOf(MOVIE_PAGE_SIZE))
                            .appendQueryParameter("offset", String.valueOf(offset)).build().toString();
                    page = new JSONObject(get(address));
                }
                List<VaultItem> movies = creator.vault.isEmpty() ? Collections.emptyList() : parseVault(page);
                for (VaultItem movie : movies) movie.studioId = creator.id;
                int followingOffset = page.optInt("next_offset", -1);
                final JSONArray genres = page.optJSONArray("genres"), ratings = page.optJSONArray("ratings");
                runOnUiThread(() -> {
                    if (isFinishing() || isDestroyed() || movieLoad != movieRequest) return;
                    moviePageLoading = false;
                    movieShelfLoaded = true;
                    nextMovieOffset = followingOffset > offset ? followingOffset : -1;
                    if (offset == 0) {
                        movieGenres = genres;
                        movieRatings = ratings;
                        vaultItems.clear();
                        vaultItems.addAll(movies);
                        renderMovies(creator);
                    } else {
                        Set<String> known = new HashSet<>();
                        for (VaultItem item : vaultItems) known.add(item.releaseKey());
                        for (VaultItem item : movies) if (known.add(item.releaseKey())) vaultItems.add(item);
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
            String availability = entry.optString("state", entry.optBoolean("online", true) ? "broadcasting" : "offline");
            if (entry.has("stream_url") && entry.optBoolean("online", true)
                    && !"offline".equals(availability) && !"reconnecting".equals(availability)) {
                String address = studioMediaAddress(entry.getString("stream_url"), guideUrl);
                if (address.isEmpty()) continue;
                Channel channel = new Channel(entry.getString("name"), address);
                channel.id = entry.optString("id");
                channel.state = availability;
                channel.fireTvUrl = studioMediaAddress(entry.optString("firetv_url"), guideUrl);
                channel.continuousUrl = studioMediaAddress(entry.optString("continuous_url"), guideUrl);
                channel.airUntil = entry.optLong("air_until_epoch_ms", 0);
                channel.number = entry.optString("number");
                int rank = entry.optInt("channel_rating_rank", 0);
                channel.ratingRank = rank >= 1 && rank <= 9 ? rank : 10;
                if (channel.ratingRank < 10) channel.rating = entry.optString("channel_rating");
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
        Set<String> seen = new HashSet<>();
        JSONArray items = value.getJSONArray("items");
        for (int i = 0; i < items.length(); i++) {
            JSONObject item = items.getJSONObject(i);
            if (!"movie".equals(item.optString("type"))) continue;
            String cover = item.optString("poster_url");
            String thumbnail = item.optString("thumbnail_url");
            VaultItem movie = new VaultItem(item.optString("title", "Untitled"), item.optString("year"),
                    item.optString("summary"), thumbnail.isEmpty() ? cover : thumbnail, cover, item.getString("stream_url"));
            movie.id = item.optString("id");
            if (seen.add(movie.releaseKey())) result.add(movie);
        }
        return result;
    }

    private static final java.util.regex.Pattern M3U_ID = java.util.regex.Pattern.compile("tvg-id=\"public-channelforge\\.([^\"]+)\"");
    private static final java.util.regex.Pattern M3U_NUMBER = java.util.regex.Pattern.compile("tvg-chno=\"([^\"]+)\"");
    private static final java.util.regex.Pattern M3U_CHANNEL_RATING = java.util.regex.Pattern.compile("tvg-rating=\"([A-Z0-9-]+)\"");
    private static final java.util.regex.Pattern M3U_CHANNEL_RANK = java.util.regex.Pattern.compile("tvg-rating-rank=\"([1-9])\"");
    private static final java.util.regex.Pattern STUDIO_SLOT_PATH = java.util.regex.Pattern.compile("^/studio/[a-f0-9]{16}(?=/)");
    private static final java.util.regex.Pattern CHANNEL_PLAYLIST_PATH = java.util.regex.Pattern.compile("/(?:broadcast|live)/[a-zA-Z0-9_-]+(?:/[^/]+)?\\.m3u8");

    private String studioMediaAddress(String raw, String guideAddress) {
        if (raw.isEmpty()) return "";
        try {
            URL guide = new URL(guideAddress);
            URL resolved = new URL(guide, raw);
            if (!"https".equals(resolved.getProtocol())) return "";
            // Studio stream paths are relative to its registered origin. A disk
            // snapshot may still include the tunnel hostname from a prior launch.
            String path = STUDIO_SLOT_PATH.matcher(resolved.getPath()).replaceFirst("");
            if (CHANNEL_PLAYLIST_PATH.matcher(path).matches()) {
                java.util.regex.Matcher slot = STUDIO_SLOT_PATH.matcher(guide.getPath());
                String prefix = slot.find() ? slot.group() : "";
                return new URL(guide, prefix + path + (resolved.getQuery() == null ? "" : "?" + resolved.getQuery())).toString();
            }
            return resolved.toString();
        } catch (IOException invalid) { return ""; }
    }

    private List<Channel> parseM3u(String m3u, String guideAddress) {
        List<Channel> result = new ArrayList<>();
        String info = "";
        for (String raw : m3u.split("\\r?\\n")) {
            String line = raw.trim();
            if (line.startsWith("#EXTINF:")) {
                info = line;
            } else if (!line.isEmpty() && !line.startsWith("#") && !info.isEmpty()) {
                int comma = info.indexOf(',');
                String address = studioMediaAddress(line, guideAddress);
                if (address.isEmpty()) { info = ""; continue; }
                Channel channel = new Channel(comma >= 0 ? info.substring(comma + 1).trim() : "Channel", address);
                java.util.regex.Matcher id = M3U_ID.matcher(info), number = M3U_NUMBER.matcher(info);
                if (id.find()) channel.id = id.group(1);
                if (number.find()) channel.number = number.group(1);
                java.util.regex.Matcher rating = M3U_CHANNEL_RATING.matcher(info);
                java.util.regex.Matcher rank = M3U_CHANNEL_RANK.matcher(info);
                if (rating.find() && rank.find()) {
                    channel.rating = rating.group(1);
                    channel.ratingRank = Integer.parseInt(rank.group(1));
                }
                result.add(channel);
                info = "";
            }
        }
        return result;
    }

    private static final long HALF_HOUR = 30 * 60 * 1000L;
    private static final int GUIDE_HOURS_AHEAD = 12;

    private void renderChannels(String creator) {
        if (activePage != Page.LIVE) {
            updateStudioStatus(creator);
            return;
        }
        View focused = getCurrentFocus();
        Object previousFocus = focused == null ? null : focused.getTag();
        liveRail.removeAllViews();
        updateStudioStatus(creator);
        renderProgramPreview();

        if (phonePortrait) {
            renderPhoneGuideControls();
            renderPhoneChannels();
            return;
        }
        LinearLayout tools = new LinearLayout(this);
        tools.setGravity(Gravity.CENTER_VERTICAL);
        guideControl(tools, phoneLayout ? "↻ SCAN CHANNELS" : "↻ SCAN FOR CHANNELS", "scan", () -> loadDirectory(), previousFocus);
        tools.addView(text(channels.size() + " channels", 11, true));
        guideControl(tools, "‹ EARLIER", "earlier", () -> moveGuide(-1), previousFocus);
        guideControl(tools, "NOW", "now", () -> { guideOffset = 0; showLoadedChannels(); }, previousFocus);
        guideControl(tools, "LATER ›", "later", () -> moveGuide(1), previousFocus);
        guideControl(tools, guideSearch.isEmpty() ? "SEARCH GUIDE" : "SEARCH: " + guideSearch,
                "search", () -> searchGuide(), previousFocus);
        guideControl(tools, guideSortByRating ? "SORT: RATING" : "SORT: CHANNEL", "guide-sort",
                this::chooseGuideSort, previousFocus);
        guideControl(tools, fullGuide ? "COMPACT GUIDE" : "FULL GUIDE", "guide-layout", () -> {
            fullGuide = !fullGuide;
            guideOffset = 0;
            showLoadedChannels();
        }, previousFocus);
        addGuideStrip(tools);
        addGap(liveRail, 6);
        LinearLayout filters = new LinearLayout(this);
        guideControl(filters, "ALL STUDIOS", "studio:", () -> filterGuide(""), previousFocus);
        for (Creator studio : reachableCreators()) {
            guideControl(filters, studio.name, "studio:" + studio.playlist,
                    () -> filterGuide(studio.playlist), previousFocus);
        }
        addGuideStrip(filters);
        addGap(liveRail, 8);

        if (channels.isEmpty()) {
            liveRail.addView(text(loadingGuides.isEmpty() ? "No live channels are reachable. Select Scan to check again."
                    : "Finding live channels…", 14, false));
            return;
        }
        if (!fullGuide) {
            renderCompactChannels(previousFocus);
            return;
        }
        int viewport = getResources().getDisplayMetrics().widthPixels
                - (menuCollapsed ? dp(phoneLayout ? 49 : 44) : navigationWidth) - dp(phoneLayout ? 35 : 63);
        int stationWidth = dp(116);
        int ticksCount = getResources().getConfiguration().screenWidthDp < 770 ? 6 : 8;
        int tickWidth = Math.max(dp(48), (viewport - stationWidth - dp(34)) / ticksCount);
        int timelineWidth = tickWidth * ticksCount;
        long start = (System.currentTimeMillis() / HALF_HOUR + 2L * guideOffset) * HALF_HOUR;
        long end = start + ticksCount * HALF_HOUR;

        LinearLayout table = new LinearLayout(this);
        table.setOrientation(LinearLayout.VERTICAL);
        table.setBackground(surface(new int[]{color("#0D142C"), color("#090A20")}, color("#1EC6E3"), 1, 7));
        if (neonTheme()) table.setBackground(phonePanel(false, 10));
        LinearLayout ticks = new LinearLayout(this);
        TextView station = sectionTitle("CH   CHANNEL", "#63EFFF");
        station.setTextSize(11);
        station.setSingleLine(true);
        station.setPadding(dp(7), dp(4), dp(3), dp(4));
        ticks.addView(station, new LinearLayout.LayoutParams(stationWidth, -2));
        for (int tick = 0; tick < ticksCount; tick++) {
            TextView label = text(guideTime(start + tick * HALF_HOUR), 11, true);
            label.setTextColor(color("#7BFAFF"));
            label.setPadding(dp(5), dp(7), dp(2), dp(7));
            ticks.addView(label, new LinearLayout.LayoutParams(tickWidth, -2));
        }
        table.addView(ticks);
        Channel initial = null;
        for (int i : displayedGuideIndices()) {
            Channel channel = channels.get(i);
            if (initial == null) initial = channel;
            addGuideRow(table, channel, i, start, end, stationWidth, timelineWidth, previousFocus);
        }
        if (initial != null && (previousFocus == null || getCurrentFocus() == focused)) showProgram(initial);
        if (initial == null) table.addView(text("No channels match this search.", 13, false));
        addGuideStrip(table);
        if (phoneLayout) addPhoneBackdrop(liveRail, 58);
    }

    private void renderPhoneGuideControls() {
        LinearLayout destinations = new LinearLayout(this);
        addPhoneGuideDestination(destinations, "Live TV\nGuide", Page.LIVE);
        addPhoneGuideDestination(destinations, "Rewind\nCinema", Page.CINEMA);
        addPhoneGuideDestination(destinations, "Continue\nWatching", Page.CONTINUE);
        addPhoneGuideDestination(destinations, "Favorites", Page.FAVORITES);
        liveRail.addView(destinations);
        addGap(liveRail, 9);
        LinearLayout actions = new LinearLayout(this);
        addPhoneAction(actions, guideSearch.isEmpty() ? "Search guide" : "Search: " + guideSearch,
                this::searchGuide);
        addPhoneAction(actions, "↻  Scan channels", this::loadDirectory);
        liveRail.addView(actions);
        addGap(liveRail, 8);

        LinearLayout filters = new LinearLayout(this);
        String studioName = "All studios";
        for (Creator studio : reachableCreators()) {
            if (studio.playlist.equals(guideStudio)) studioName = studio.name;
        }
        addPhoneAction(filters, studioName + " ▾", this::choosePhoneGuideStudio);
        addPhoneAction(filters, (guideOffset == 0 ? "Now" : guideTime(phoneGuideTime())) + " ▾",
                this::choosePhoneGuideTime);
        addPhoneAction(filters, guideSortByRating ? "Rating ▾" : "Sort ▾", this::chooseGuideSort);
        liveRail.addView(filters);
        addGap(liveRail, 7);
    }

    private List<JSONObject> upcomingPrograms(Channel channel, long now) {
        List<JSONObject> next = new ArrayList<>();
        if (channel.programs == null) return next;
        for (int i = 0; i < channel.programs.length() && next.size() < 2; i++) {
            JSONObject entry = channel.programs.optJSONObject(i);
            if (entry != null && guideTimeMillis(entry, "start") > now) next.add(entry);
        }
        return next;
    }

    private void renderCompactChannels(Object previousFocus) {
        long now = System.currentTimeMillis();
        List<Integer> displayed = displayedGuideIndices();
        if (displayed.isEmpty()) {
            liveRail.addView(text("No channels match this search.", 13, false));
            return;
        }
        showProgram(channels.get(displayed.get(0)));
        for (int index : displayed) {
            Channel channel = channels.get(index);
            JSONObject current = programAt(channel, now);
            List<JSONObject> next = upcomingPrograms(channel, now);
            LinearLayout row = new LinearLayout(this);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(dp(5), dp(4), dp(5), dp(4));
            row.setBackground(phonePanel(false, 8));
            String art = channelArtwork(channel, current);
            if (!art.isEmpty()) {
                ImageView artwork = new ImageView(this);
                artwork.setScaleType(ImageView.ScaleType.CENTER_CROP);
                artwork.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
                artwork.setBackground(phonePanel(false, 7));
                artwork.setClipToOutline(true);
                row.addView(artwork, new LinearLayout.LayoutParams(dp(phoneLayout ? 73 : 110), dp(phoneLayout ? 47 : 59)));
                posterLoader.execute(() -> loadPoster(art, artwork));
            }
            addCompactCell(row, "CH " + displayNumber(channel, index) + guideRatingLabel(channel), channel.name, 1.2f);
            addCompactCell(row, "On Now", current == null ? "Schedule unavailable" : current.optString("title"), 1f);
            addCompactCell(row, "Up Next", next.isEmpty() ? "—" : next.get(0).optString("title"), 1f);
            if (getResources().getConfiguration().screenWidthDp >= 850)
                addCompactCell(row, "Later", next.size() < 2 ? "—" : next.get(1).optString("title"), 1f);
            boolean saved = isFavorite(channel);
            TextView star = navigationButton(saved ? "★" : "☆");
            star.setContentDescription("Favorite " + channel.name);
            star.setOnClickListener(view -> toggleFavorite(channel, star));
            row.addView(star);
            String tag = "compact-watch:" + channel.url;
            guideControl(row, "▶ WATCH LIVE", tag, () -> play(channel), previousFocus);
            View watch = row.findViewWithTag(tag);
            watch.setContentDescription("Watch live: " + channel.name);
            watch.setOnFocusChangeListener((view, focused) -> {
                if (focused) { playMenuSound(); showProgram(channel); }
            });
            liveRail.addView(row);
            addGap(liveRail, 5);
        }
    }

    private void addCompactCell(LinearLayout row, String label, String value, float weight) {
        LinearLayout cell = new LinearLayout(this);
        cell.setOrientation(LinearLayout.VERTICAL);
        cell.setPadding(dp(5), 0, dp(5), 0);
        TextView heading = text(label, 10, true);
        heading.setTextColor(color("#6BEAFF"));
        heading.setSingleLine(true);
        TextView title = text(value, 13, true);
        title.setSingleLine(true);
        title.setEllipsize(TextUtils.TruncateAt.END);
        cell.addView(heading);
        cell.addView(title);
        row.addView(cell, new LinearLayout.LayoutParams(0, -2, weight));
    }

    private void addPhoneGuideDestination(LinearLayout row, String label, Page page) {
        boolean selected = page == Page.LIVE;
        LinearLayout button = new LinearLayout(this);
        button.setOrientation(LinearLayout.VERTICAL);
        button.setGravity(Gravity.CENTER);
        button.setMinimumHeight(dp(60));
        button.setPadding(dp(3), dp(4), dp(3), dp(4));
        button.setBackground(phonePanel(selected, 9));
        button.setFocusable(true);
        button.setContentDescription(label.replace('\n', ' '));
        button.setOnClickListener(v -> openPage(page));
        button.addView(navigationGlyph(page, selected), new LinearLayout.LayoutParams(dp(24), dp(24)));
        TextView title = text(label, 12, true);
        title.setGravity(Gravity.CENTER);
        title.setPadding(0, dp(2), 0, 0);
        button.addView(title);
        LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(0, -1, 1);
        if (row.getChildCount() > 0) layout.setMarginStart(dp(6));
        row.addView(button, layout);
    }

    private void addPhoneAction(LinearLayout row, String label, Runnable action) {
        TextView button = navigationButton(label);
        button.setTextSize(12);
        button.setBackground(phonePanel(false, 7));
        button.setOnClickListener(v -> action.run());
        addPhoneAction(row, button);
    }

    private void addPhoneAction(LinearLayout row, TextView button) {
        button.setGravity(Gravity.CENTER);
        button.setSingleLine(false);
        button.setMaxLines(2);
        button.setEllipsize(android.text.TextUtils.TruncateAt.END);
        button.setMinHeight(dp(48));
        LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(0, -1, 1);
        if (row.getChildCount() > 0) layout.setMarginStart(dp(8));
        row.addView(button, layout);
    }

    private void choosePhoneGuideStudio() {
        List<Creator> options = reachableCreators();
        String[] names = new String[options.size() + 1];
        names[0] = "All studios";
        int selected = 0;
        for (int i = 0; i < options.size(); i++) {
            Creator studio = options.get(i);
            names[i + 1] = studio.name;
            if (studio.playlist.equals(guideStudio)) selected = i + 1;
        }
        new AlertDialog.Builder(this).setTitle("Filter by studio").setSingleChoiceItems(names, selected, (dialog, which) -> {
            dialog.dismiss();
            filterGuide(which == 0 ? "" : options.get(which - 1).playlist);
        }).setNegativeButton("Cancel", null).show();
    }

    private long phoneGuideTime() {
        return System.currentTimeMillis() + 2L * guideOffset * HALF_HOUR;
    }

    private void choosePhoneGuideTime() {
        long now = System.currentTimeMillis();
        String[] times = new String[GUIDE_HOURS_AHEAD + 2];
        for (int i = 0; i < times.length; i++) {
            times[i] = i == 1 ? "Now" : guideTime(now + 2L * (i - 1) * HALF_HOUR);
        }
        new AlertDialog.Builder(this).setTitle("Guide time").setSingleChoiceItems(times, guideOffset + 1, (dialog, which) -> {
            dialog.dismiss();
            guideOffset = which - 1;
            showLoadedChannels();
        }).setNegativeButton("Cancel", null).show();
    }

    private void renderPhoneChannels() {
        if (channels.isEmpty()) {
            liveRail.addView(text(loadingGuides.isEmpty() ? "No live channels are reachable. Select Scan to check again."
                    : "Finding live channels…", 14, false));
            return;
        }
        long guideTime = phoneGuideTime();
        List<Integer> displayed = displayedGuideIndices();
        liveRail.addView(sectionTitle("CHANNELS (" + displayed.size() + ")", "#ECF7FF"));
        Channel first = null;
        for (int i : displayed) {
            Channel channel = channels.get(i);
            if (first == null) first = channel;
            LinearLayout card = new LinearLayout(this);
            card.setGravity(Gravity.CENTER_VERTICAL);
            card.setBackground(phonePanel((i & 1) == 1, 9));
            card.setPadding(dp(7), dp(6), dp(6), dp(6));
            FrameLayout mark = new FrameLayout(this);
            mark.setBackground(phonePanel((i & 1) == 1, 7));
            mark.setClipToOutline(true);
            TextView markLabel = text("CH " + displayNumber(channel, i), 13, true);
            markLabel.setGravity(Gravity.CENTER);
            markLabel.setTextColor(color("#52F0FB"));
            mark.addView(markLabel, new FrameLayout.LayoutParams(-1, -1));
            JSONObject scheduled = programAt(channel, guideTime);
            String art = channelArtwork(channel, scheduled);
            if (!art.isEmpty()) {
                ImageView logo = new ImageView(this);
                logo.setScaleType(ImageView.ScaleType.CENTER_CROP);
                logo.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
                mark.addView(logo, new FrameLayout.LayoutParams(-1, -1));
                posterLoader.execute(() -> loadPoster(art, logo));
            }
            card.addView(mark, new LinearLayout.LayoutParams(dp(83), dp(68)));
            LinearLayout identity = new LinearLayout(this);
            identity.setOrientation(LinearLayout.VERTICAL);
            identity.setPadding(dp(8), 0, 0, 0);
            TextView station = text("CH " + displayNumber(channel, i) + guideRatingLabel(channel) + "  •  " + channel.studio, 11, false);
            station.setTextColor(color("#8DBAD5"));
            station.setPadding(0, 0, 0, 0);
            station.setMaxLines(1);
            station.setEllipsize(TextUtils.TruncateAt.END);
            identity.addView(station);
            TextView name = text(channel.name, 16, true);
            name.setPadding(0, dp(2), 0, 0);
            identity.addView(name);
            String program = scheduled == null ? "No program listed" : scheduled.optString("title", "No program listed");
            TextView summary = text(program, 12, false);
            summary.setTextColor(color("#BED3E8"));
            summary.setPadding(0, dp(2), 0, dp(2));
            summary.setMaxLines(2);
            summary.setEllipsize(TextUtils.TruncateAt.END);
            identity.addView(summary);
            List<JSONObject> upcoming = upcomingPrograms(channel, guideTime);
            if (!upcoming.isEmpty()) {
                TextView next = text("Next: " + upcoming.get(0).optString("title"), 11, false);
                next.setMaxLines(1);
                next.setEllipsize(TextUtils.TruncateAt.END);
                next.setPadding(0, dp(2), 0, 0);
                identity.addView(next);
            }
            if (guideOffset == 0 && scheduled != null) {
                long scheduledStart = guideTimeMillis(scheduled, "start");
                long duration = guideTimeMillis(scheduled, "stop") - scheduledStart;
                if (duration > 0) {
                    ProgressBar progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
                    progress.setMax(1000);
                    progress.setProgress((int) Math.min(1000, Math.max(0, 1000 * (guideTime - scheduledStart) / duration)));
                    progress.setProgressTintList(android.content.res.ColorStateList.valueOf(color("#1FE3F9")));
                    identity.addView(progress, new LinearLayout.LayoutParams(-1, dp(5)));
                }
            }
            card.addView(identity, new LinearLayout.LayoutParams(0, -2, 1));
            LinearLayout controls = new LinearLayout(this);
            controls.setPadding(dp(5), 0, 0, 0);
            controls.setOrientation(LinearLayout.VERTICAL);
            boolean saved = isFavorite(channel);
            TextView favorite = navigationButton(saved ? "★" : "☆");
            favorite.setContentDescription((saved ? "Remove from favorites: " : "Add to favorites: ") + channel.name);
            favorite.setGravity(Gravity.CENTER);
            favorite.setPadding(0, 0, 0, 0);
            favorite.setTextSize(19);
            favorite.setMinHeight(dp(41));
            favorite.setBackgroundColor(Color.TRANSPARENT);
            favorite.setOnClickListener(v -> toggleFavorite(channel, favorite));
            controls.addView(favorite, new LinearLayout.LayoutParams(dp(44), -2));
            TextView watch = navigationButton("▶");
            watch.setContentDescription("Watch live: " + channel.name);
            watch.setGravity(Gravity.CENTER);
            watch.setPadding(0, 0, 0, 0);
            watch.setTextSize(17);
            watch.setMinHeight(dp(42));
            watch.setBackground(phonePanel(false, 7));
            watch.setOnClickListener(v -> play(channel));
            controls.addView(watch, new LinearLayout.LayoutParams(dp(44), -2));
            card.addView(controls);
            liveRail.addView(card);
            addGap(liveRail, 10);
        }
        if (first == null) liveRail.addView(text("No channels match this search.", 13, false));
        else showProgram(first);
        addPhoneBackdrop(liveRail, 90);
    }

    private void addGuideStrip(LinearLayout contents) {
        addHorizontalStrip(liveRail, contents);
    }

    private void addHorizontalStrip(LinearLayout parent, LinearLayout contents) {
        HorizontalScrollView viewport = new HorizontalScrollView(this);
        viewport.setHorizontalScrollBarEnabled(false);
        viewport.setFillViewport(true);
        viewport.addView(contents);
        parent.addView(viewport);
    }


    private void guideControl(LinearLayout row, String label, String id, Runnable action, Object previous) {
        TextView button = navigationButton(label);
        button.setTag(id);
        button.setOnClickListener(view -> action.run());
        if (id.equals("studio:" + guideStudio) || (guideOffset == 0 && "now".equals(id))) {
            button.setTextColor(color("#FFF088"));
        }
        LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(-2, -2);
        layout.setMargins(0, 0, dp(5), 0);
        row.addView(button, layout);
        if (id.equals(previous)) button.requestFocus();
    }

    private void moveGuide(int direction) {
        fullGuide = true;
        guideOffset = Math.max(-1, Math.min(GUIDE_HOURS_AHEAD, guideOffset + direction));
        showLoadedChannels();
    }

    private void filterGuide(String playlist) {
        guideStudio = playlist;
        showLoadedChannels();
    }

    private void chooseGuideSort() {
        new AlertDialog.Builder(this).setTitle("Sort live channels")
                .setSingleChoiceItems(new String[]{"Channel order", "Rating, lowest first"}, guideSortByRating ? 1 : 0,
                        (dialog, which) -> {
                            guideSortByRating = which == 1;
                            dialog.dismiss();
                            showLoadedChannels();
                        })
                .setNegativeButton("Cancel", null).show();
    }

    private List<Integer> displayedGuideIndices() {
        List<Integer> indices = new ArrayList<>();
        for (int i = 0; i < channels.size(); i++) {
            if (matchesGuide(channels.get(i))) indices.add(i);
        }
        if (guideSortByRating) Collections.sort(indices, (left, right) ->
                Integer.compare(channels.get(left).ratingRank, channels.get(right).ratingRank));
        return indices;
    }

    private String guideRatingLabel(Channel channel) {
        if (!channel.rating.isEmpty()) return "  •  " + channel.rating;
        return guideSortByRating ? "  •  Unrated" : "";
    }

    private void searchGuide() {
        EditText query = new EditText(this);
        query.setSingleLine(true);
        query.setHint("Channel, studio, or program");
        query.setText(guideSearch);
        new AlertDialog.Builder(this).setTitle("Search the guide").setView(query)
                .setNegativeButton("Cancel", null).setPositiveButton("Search", (dialog, which) -> {
                    guideSearch = query.getText().toString().trim();
                    showLoadedChannels();
                }).show();
    }

    private boolean matchesGuide(Channel channel) {
        String query = guideSearch.toLowerCase(Locale.ROOT);
        if (query.isEmpty() || (channel.name + " " + channel.studio).toLowerCase(Locale.ROOT).contains(query)) return true;
        if (channel.programs == null) return false;
        for (int i = 0; i < channel.programs.length(); i++) {
            JSONObject program = channel.programs.optJSONObject(i);
            if (program != null && program.optString("title").toLowerCase(Locale.ROOT).contains(query)) return true;
        }
        return false;
    }

    private void renderProgramPreview() {
        LinearLayout preview = new LinearLayout(this);
        preview.setBaselineAligned(false);
        TextView screen = text("SELECT A CHANNEL FROM THE GUIDE", 9, true);
        screen.setGravity(Gravity.CENTER);
        screen.setLetterSpacing(.13f);
        screen.setTextColor(color("#A1D5E8"));
        screen.setBackground(surface(new int[]{color("#1C1944"), color("#160E26"), color("#080818")},
                color("#26D6F2"), 1, 8));
        LinearLayout.LayoutParams screenLayout = new LinearLayout.LayoutParams(0, -1, .62f);
        screenLayout.setMargins(0, 0, dp(8), 0);
        landscapeProgramArtwork = null;
        if (neonTheme() && !phonePortrait) {
            FrameLayout cover = new FrameLayout(this);
            cover.setBackground(phonePanel(true, 9));
            cover.setClipToOutline(true);
            landscapeProgramArtwork = new ImageView(this);
            landscapeProgramArtwork.setScaleType(ImageView.ScaleType.CENTER_CROP);
            landscapeProgramArtwork.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
            cover.addView(landscapeProgramArtwork, new FrameLayout.LayoutParams(-1, -1));
            TextView nowLabel = sectionTitle("▶  NOW PLAYING", "#B3F6FF");
            nowLabel.setBackground(phonePanel(true, 5));
            nowLabel.setPadding(dp(7), dp(3), dp(7), dp(3));
            FrameLayout.LayoutParams tag = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.START);
            tag.setMargins(dp(8), dp(7), dp(8), 0);
            cover.addView(nowLabel, tag);
            preview.addView(cover, screenLayout);
        } else if (!phonePortrait) preview.addView(screen, screenLayout);
        FrameLayout card = new FrameLayout(this);
        card.setBackground(surface(new int[]{color("#10162C"), color("#2C1350")}, color("#26D6F2"), 1, 8));
        if (neonTheme()) card.setBackground(phonePanel(phonePortrait, 11));
        if (programArtwork != null) programArtwork.removeCallbacks(previewClock);
        previewChannel = null;
        programArtwork = new ImageView(this);
        programArtwork.setScaleType(ImageView.ScaleType.CENTER_CROP);
        programArtwork.setAlpha(.86f);
        ImageView previewArtwork = programArtwork;
        FrameLayout.LayoutParams artworkLayout = new FrameLayout.LayoutParams(-1, -1, Gravity.RIGHT);
        card.addView(programArtwork, artworkLayout);
        card.addOnLayoutChangeListener((view, left, top, right, bottom, oldLeft, oldTop, oldRight, oldBottom) -> {
            int width = Math.round((right - left) * .68f);
            if (width > 0 && artworkLayout.width != width) {
                artworkLayout.width = width;
                previewArtwork.setLayoutParams(artworkLayout);
            }
        });
        View artworkShade = new View(this);
        artworkShade.setBackground(new GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT,
                new int[]{color("#FF0A1328"), color("#F80A1328"), color("#800A1328"), color("#220A1328")}));
        card.addView(artworkShade, new FrameLayout.LayoutParams(-1, -1));
        card.setClipToOutline(true);
        LinearLayout detail = new LinearLayout(this);
        detail.setOrientation(LinearLayout.VERTICAL);
        detail.setPadding(dp(8), dp(phonePortrait ? 11 : 4), dp(8), dp(phonePortrait ? 9 : 2));
        TextView now = sectionTitle("▶  NOW PLAYING", "#5EF4FF");
        now.setPadding(dp(6), dp(1), dp(6), dp(1));
        if (phonePortrait) now.setBackground(phonePanel(true, 5));
        if (phonePortrait || !neonTheme()) detail.addView(now, new LinearLayout.LayoutParams(-2, -2));
        programTitle = text("Select a channel from the guide", 17, true);
        programTitle.setTextColor(color(neonTheme() ? "#F3F8FF" : "#FFF16B"));
        if (neonTheme()) programTitle.setTypeface(Typeface.create("sans-serif-condensed", Typeface.BOLD_ITALIC));
        if (phonePortrait) programTitle.setMaxLines(2);
        else programTitle.setSingleLine(true);
        programTitle.setPadding(dp(6), dp(1), dp(6), dp(2));
        programTitle.setEllipsize(android.text.TextUtils.TruncateAt.END);
        detail.addView(programTitle);
        programTime = text("", 12, true);
        programTime.setTextColor(color("#AECCE9"));
        programTime.setPadding(dp(6), 0, dp(6), dp(2));
        detail.addView(programTime);
        programProgress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        programProgress.setMax(1000);
        programProgress.setProgressTintList(android.content.res.ColorStateList.valueOf(color("#42F9DC")));
        detail.addView(programProgress, new LinearLayout.LayoutParams(-1, dp(5)));
        LinearLayout timing = new LinearLayout(this);
        programElapsed = text("", 9, true);
        programDuration = text("", 9, true);
        programElapsed.setPadding(dp(6), dp(1), dp(6), 0);
        programDuration.setPadding(dp(6), dp(1), dp(6), 0);
        timing.addView(programElapsed, new LinearLayout.LayoutParams(0, -2, 1));
        timing.addView(programDuration);
        detail.addView(timing);
        card.addView(detail, new FrameLayout.LayoutParams(-1, -2));
        if (phonePortrait) {
            card.setMinimumHeight(dp(131));
            preview.addView(card, new LinearLayout.LayoutParams(-1, -2));
            liveRail.addView(preview, new LinearLayout.LayoutParams(-1, -2));
        } else {
            preview.addView(card, new LinearLayout.LayoutParams(0, -1, 1));
            int previewHeight = getResources().getConfiguration().screenHeightDp < 420 ? 87 : 106;
            liveRail.addView(preview, new LinearLayout.LayoutParams(-1, dp(previewHeight)));
        }
        addGap(liveRail, phonePortrait ? 12 : 8);
    }

    private String guideTime(long milliseconds) {
        return android.text.format.DateFormat.getTimeFormat(this).format(new Date(milliseconds));
    }

    private void showProgram(Channel channel) {
        previewChannel = channel;
        programArtwork.removeCallbacks(previewClock);
        programArtwork.postDelayed(previewClock, 1000);
        updateProgram(channel);
    }

    private JSONObject programAt(Channel channel, long time) {
        if (channel.programs == null) return null;
        for (int i = 0; i < channel.programs.length(); i++) {
            JSONObject entry = channel.programs.optJSONObject(i);
            if (entry != null && guideTimeMillis(entry, "start") <= time && guideTimeMillis(entry, "stop") > time)
                return entry;
        }
        return null;
    }

    private void updateProgram(Channel channel) {
        long now = System.currentTimeMillis();
        JSONObject item = programAt(channel, now);
        programTitle.setText(item == null ? channel.name : item.optString("title", channel.name));
        programTime.setText(channel.studio + (channel.number.isEmpty() ? "" : "  •  CH " + channel.number)
                + (item == null || item.optString("content_rating").isEmpty() ? "" : "  •  " + item.optString("content_rating")));
        long duration = item == null ? 0 : guideTimeMillis(item, "stop") - guideTimeMillis(item, "start");
        long elapsed = item == null ? 0 : now - guideTimeMillis(item, "start");
        programProgress.setProgress(duration <= 0 ? 0 : (int) Math.min(1000, 1000 * elapsed / duration));
        programElapsed.setText(duration <= 0 ? "" : elapsedTime(elapsed));
        programDuration.setText(duration <= 0 ? "" : elapsedTime(duration));
        String[] artwork = item == null ? new String[0] : new String[]{
                item.optString("backdrop_url"), item.optString("series_poster_url"), item.optString("poster_url")};
        String artworkKey = android.text.TextUtils.join("\n", artwork);
        if (artworkKey.equals(programArtwork.getTag())) return;
        ImageView view = programArtwork;
        ImageView wideArtwork = landscapeProgramArtwork;
        view.setTag(artworkKey);
        view.setImageDrawable(null);
        if (wideArtwork != null) wideArtwork.setImageDrawable(null);
        if (!artworkKey.trim().isEmpty()) posterLoader.execute(() -> {
            Set<String> tried = new HashSet<>();
            for (String url : artwork) {
                if (url.isEmpty() || !tried.add(url)) continue;
                Bitmap image = posterBitmap(url);
                if (image == null) continue;
                runOnUiThread(() -> {
                    if (view.isAttachedToWindow() && artworkKey.equals(view.getTag())) {
                        view.setImageBitmap(image);
                        if (wideArtwork != null) wideArtwork.setImageBitmap(image);
                    }
                });
                break;
            }
        });
    }

    private String elapsedTime(long milliseconds) {
        long seconds = milliseconds / 1000;
        return String.format(Locale.US, "%d:%02d", seconds / 60, seconds % 60);
    }

    private void addGuideRow(LinearLayout table, Channel channel, int index, long start, long end,
                             int stationWidth, int timelineWidth, Object previous) {
        LinearLayout row = new LinearLayout(this);
        row.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout tune = new LinearLayout(this);
        tune.setGravity(Gravity.CENTER_VERTICAL);
        tune.setFocusable(true);
        tune.setTag(channel.url);
        tune.setContentDescription("CH " + displayNumber(channel, index) + " " + channel.name + ". Press OK to watch.");
        tune.setBackground(surface(new int[]{color("#101831"), color("#0B0B23")}, color("#2B597F"), 1, 4));
        String availability = channel.state;
        Creator owner = studioFor(channel);
        if (owner != null && !owner.state.equals("broadcasting") && !owner.state.equals("standby")) availability = owner.state;
        TextView station = text("CH " + displayNumber(channel, index) + "  " + channel.name + "\n" + channel.studio + guideRatingLabel(channel)
                + ("broadcasting".equals(availability) ? "" : " · " + availability), 11, true);
        station.setPadding(dp(7), dp(4), dp(4), dp(4));
        station.setMaxLines(3);
        station.setEllipsize(android.text.TextUtils.TruncateAt.END);
        station.setTextColor(color("#77EFFF"));
        tune.addView(station, new LinearLayout.LayoutParams(stationWidth, -1));
        LinearLayout programs = new LinearLayout(this);
        int cursor = 0;
        long now = System.currentTimeMillis();
        if (channel.programs != null) {
            for (int i = 0; i < channel.programs.length(); i++) {
                JSONObject entry = channel.programs.optJSONObject(i);
                if (entry == null) continue;
                long from = guideTimeMillis(entry, "start"), until = guideTimeMillis(entry, "stop");
                if (from >= end || until <= start) continue;
                int left = (int) ((Math.max(start, from) - start) * timelineWidth / (end - start));
                int right = (int) ((Math.min(end, until) - start) * timelineWidth / (end - start));
                if (right <= cursor) continue;
                if (left > cursor) programs.addView(new Space(this), new LinearLayout.LayoutParams(left - cursor, 1));
                LinearLayout cell = programCell(entry.optString("title", "Scheduled program"), from, until, from <= now && now < until);
                programs.addView(cell, new LinearLayout.LayoutParams(right - Math.max(left, cursor), -1));
                cursor = right;
            }
        }
        if (cursor == 0) programs.addView(text("Schedule unavailable", 12, false), new LinearLayout.LayoutParams(timelineWidth, -1));
        tune.addView(programs, new LinearLayout.LayoutParams(timelineWidth, -1));
        tune.setOnFocusChangeListener((view, active) -> {
            tune.setBackground(surface(new int[]{color(active ? "#2A2256" : "#101831"), color("#0B0B23")},
                    color(active ? "#FF51C7" : "#2B597F"), active ? 2 : 1, 4));
            if (active) showProgram(channel);
        });
        if (phoneLayout) tune.setContentDescription("Watch live: " + channel.name);
        tune.setOnClickListener(view -> play(channel));
        row.addView(tune, new LinearLayout.LayoutParams(stationWidth + timelineWidth, dp(45)));
        TextView star = navigationButton(isFavorite(channel) ? "★" : "☆");
        star.setContentDescription("Toggle favorite: " + channel.name);
        star.setOnClickListener(view -> toggleFavorite(channel, star));
        row.addView(star, new LinearLayout.LayoutParams(dp(34), dp(45)));
        table.addView(row);
        if (channel.url.equals(previous)) tune.requestFocus();
    }

    private LinearLayout programCell(String title, long start, long stop, boolean current) {
        LinearLayout cell = new LinearLayout(this);
        cell.setOrientation(LinearLayout.VERTICAL);
        cell.setGravity(Gravity.CENTER_VERTICAL);
        cell.setPadding(dp(4), 0, dp(3), 0);
        cell.setBackground(surface(new int[]{color(current ? "#352C25" : "#111B34"), color("#101126")},
                color(current ? "#F2CD37" : "#364D6C"), 1, 3));
        TextView name = text(title, 11, true);
        name.setTextColor(color(current ? "#FFF087" : "#E4E7F4"));
        TextView time = text(guideTime(start) + " – " + guideTime(stop), 9, false);
        time.setTextColor(color("#ABC3DF"));
        for (TextView line : new TextView[]{name, time}) {
            line.setSingleLine(true);
            line.setEllipsize(android.text.TextUtils.TruncateAt.END);
            line.setPadding(0, 0, 0, 0);
            cell.addView(line);
        }
        return cell;
    }


    private String[] currentAndNext(Channel channel) {
        String[] titles = {"", ""};
        if (channel.programs == null) return titles;
        long now = System.currentTimeMillis();
        for (int i = 0; i < channel.programs.length(); i++) {
            JSONObject current = channel.programs.optJSONObject(i);
            if (current == null || guideTimeMillis(current, "start") > now || guideTimeMillis(current, "stop") <= now)
                continue;
            titles[0] = current.optString("title");
            JSONObject next = channel.programs.optJSONObject(i + 1);
            if (next != null) titles[1] = next.optString("title");
            break;
        }
        return titles;
    }

    private void updateStudioStatus(String creator) {
        if (phonePortrait) {
            String freshness = directoryStale || !staleGuides.isEmpty() ? "  •  Last known guide" : "";
            if (!cinemaScreen) {
                int count = channels.size();
                status.setText(count + (count == 1 ? " channel" : " channels") + freshness);
            } else if (movieShelfLoaded) {
                status.setText(vaultItems.size() + (nextMovieOffset >= 0 ? "+" : "")
                        + (vaultItems.size() == 1 && nextMovieOffset < 0 ? " movie" : " movies"));
            } else {
                status.setText(moviePageLoading ? "Finding movies…" : "Rewind Cinema");
            }
            return;
        }
        status.setText("●  " + creator.toUpperCase(Locale.US) + "  •  " + channels.size()
                + " CHANNELS" + (directoryStale || !staleGuides.isEmpty() ? "  •  LAST KNOWN GUIDE" : "") + "  •  " + (movieShelfLoaded
                ? vaultItems.size() + (nextMovieOffset >= 0 ? "+" : "") + " MOVIES"
                : moviePageLoading ? "CHECKING MOVIES" : "REWIND CINEMA"));
    }

    private void reloadMovieFilters(Creator creator) {
        moviePageLoading = false;
        moreMovies = null;
        nextMovieOffset = -1;
        status.setText("Searching " + creator.name + "…");
        loadMoviePage(creator, 0);
    }

    private void addMovieFilters(Creator creator) {
        String[] decades = {"all", "60s", "70s", "80s", "90s", "2000s", "recent"};
        String[] labels = {"All decades", "60s", "70s", "80s", "90s", "2000s", "2010+"};
        LinearLayout strip = new LinearLayout(this);
        TextView search = navigationButton(movieSearch.isEmpty() ? "Search movies" : movieSearch);
        search.setOnClickListener(v -> searchMovies(creator));
        if (phonePortrait) {
            int selected = Math.max(0, java.util.Arrays.asList(decades).indexOf(movieDecade));
            addPhoneAction(strip, search);
            addPhoneAction(strip, labels[selected] + " ▾", () -> new AlertDialog.Builder(this)
                    .setTitle("Filter by decade").setSingleChoiceItems(labels, selected, (dialog, which) -> {
                        dialog.dismiss();
                        movieDecade = decades[which];
                        reloadMovieFilters(creator);
                    }).setNegativeButton("Cancel", null).show());
            movieRail.addView(strip);
            addGap(movieRail, 8);
            LinearLayout metadata = new LinearLayout(this);
            addMovieChoices(metadata, creator, true);
            addMovieChoices(metadata, creator, false);
            if (metadata.getChildCount() > 0) {
                movieRail.addView(metadata);
                addGap(movieRail, 8);
            }
            return;
        }
        for (int i = 0; i < decades.length; i++) {
            String value = decades[i], label = i == 0 ? "ALL" : labels[i].toUpperCase(Locale.ROOT);
            TextView button = navigationButton(label);
            button.setSelected(value.equals(movieDecade));
            if (button.isSelected()) button.setTextColor(color("#FFF088"));
            button.setContentDescription(label + (button.isSelected() ? ", selected" : ""));
            button.setOnClickListener(v -> { movieDecade = value; reloadMovieFilters(creator); });
            strip.addView(button);
        }
        strip.addView(search);
        addMovieChoices(strip, creator, true);
        addMovieChoices(strip, creator, false);
        addHorizontalStrip(movieRail, strip);
    }

    private void searchMovies(Creator creator) {
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setText(movieSearch);
        new AlertDialog.Builder(this).setTitle("Search movies").setView(input)
                .setPositiveButton("Search", (d,w) -> { movieSearch = input.getText().toString().trim(); reloadMovieFilters(creator); })
                .setNeutralButton("Clear", (d,w) -> { movieSearch = ""; reloadMovieFilters(creator); })
                .setNegativeButton("Cancel", null).show();
    }

    private void addMovieChoices(LinearLayout row, Creator creator, boolean genres) {
        JSONArray options = genres ? movieGenres : movieRatings;
        if (options == null || options.length() == 0) return;
        String all = genres ? "All genres" : "All ratings";
        String selected = genres ? movieGenre : movieRating;
        TextView button = navigationButton(selected.isEmpty() ? all : selected);
        String[] names = new String[options.length() + 1];
        names[0] = all;
        for (int i = 0; i < options.length(); i++) names[i + 1] = options.optString(i);
        button.setOnClickListener(v -> new AlertDialog.Builder(this).setTitle(all)
                .setItems(names, (d,index) -> {
                    String value = index == 0 ? "" : names[index];
                    if (genres) movieGenre = value; else movieRating = value;
                    reloadMovieFilters(creator);
                }).show());
        if (phonePortrait) addPhoneAction(row, button);
        else row.addView(button);
    }

    private void renderMovies(Creator creator) {
        movieRail.removeAllViews();
        updateStudioStatus(creator.name);
        FrameLayout hero = new FrameLayout(this);
        hero.setBackground(surface(new int[]{color("#10172D"), color("#2A153C")}, color("#38DDF5"), 1, 11));
        hero.setClipToOutline(true);
        ImageView artwork = new ImageView(this);
        artwork.setImageResource(R.drawable.cinema_hero);
        artwork.setScaleType(ImageView.ScaleType.CENTER_CROP);
        hero.addView(artwork, new FrameLayout.LayoutParams(-1, -1));
        TextView title = sectionTitle("REWIND CINEMA", "#F5F7FF");
        android.text.SpannableString heading = new android.text.SpannableString("REWIND CINEMA");
        heading.setSpan(new android.text.style.ForegroundColorSpan(color("#FF48C8")), 7, heading.length(), 0);
        title.setText(heading);
        title.setTypeface(Typeface.DEFAULT, Typeface.BOLD_ITALIC);
        title.setTextSize(compactTouchLayout ? 25 : 32);
        title.setGravity(Gravity.CENTER);
        hero.addView(title, new FrameLayout.LayoutParams(-1, -1));
        movieRail.addView(hero, new LinearLayout.LayoutParams(-1, dp(compactTouchLayout ? 62 : 84)));
        addGap(movieRail, 6);
        LinearLayout studios = new LinearLayout(this);
        for (Creator option : reachableCreators()) {
            if (!option.cinemaSupported) continue;
            TextView studio = navigationButton(option.name);
            if (option.playlist.equals(creator.playlist)) studio.setTextColor(color("#FFF088"));
            studio.setOnClickListener(view -> loadCreator(option));
            LinearLayout.LayoutParams button = new LinearLayout.LayoutParams(-2, -2);
            button.setMargins(0, 0, dp(5), 0);
            studios.addView(studio, button);
        }
        if (studios.getChildCount() > 1) {
            addHorizontalStrip(movieRail, studios);
            if (phonePortrait) addGap(movieRail, 8);
        }
        addMovieFilters(creator);
        if (vaultItems.isEmpty()) {
            movieRail.addView(text("No movies match this selection.", 14, false));
            return;
        }
        LinearLayout content = new LinearLayout(this);
        if (compactTouchLayout) content.setOrientation(LinearLayout.VERTICAL);
        content.setBaselineAligned(false);
        content.setPadding(0, dp(8), 0, 0);
        int screenHeight = getResources().getConfiguration().screenHeightDp;
        int height = Math.max(106, Math.min(430, screenHeight - (screenHeight < 450 ? 270 : 318)));
        LinearLayout shelf = new LinearLayout(this);
        shelf.setOrientation(LinearLayout.VERTICAL);
        movieShelf = shelf;
        shelf.setPadding(dp(4), dp(4), dp(6), dp(8));
        if (phoneLayout) {
            shelf.setBackground(surface(new int[]{color("#111D34"), color("#100D22")}, color("#4F9CC3"), 1, 9));
            movieRail.addView(shelf, new LinearLayout.LayoutParams(-1, -2));
            growMovieShelf(creator);
            return;
        }
        ScrollView results = new ScrollView(this);
        results.setBackground(surface(new int[]{color("#111D34"), color("#100D22")}, color("#4F9CC3"), 1, 9));
        results.setFillViewport(true);
        results.setNestedScrollingEnabled(true);
        results.addView(shelf);
        content.addView(results, compactTouchLayout ? new LinearLayout.LayoutParams(-1, dp(Math.max(105, screenHeight / 3)))
                : new LinearLayout.LayoutParams(0, dp(height), 1.15f));
        movieDetails = panel();
        movieDetails.setBackground(surface(new int[]{color("#21103F"), color("#101D31"), color("#0C1124")},
                color("#2CD9F2"), 1, 10));
        ScrollView detailsScroll = new ScrollView(this);
        detailsScroll.setFillViewport(true);
        detailsScroll.setNestedScrollingEnabled(true);
        detailsScroll.addView(movieDetails);
        content.addView(detailsScroll, compactTouchLayout ? new LinearLayout.LayoutParams(-1, -2)
                : new LinearLayout.LayoutParams(0, dp(height), 1f));
        movieRail.addView(content);
        growMovieShelf(creator);
        insertMovie = null;
        movieDetails.addView(text("Select a movie to see its details.", 14, false));
    }

    private void showMovieDetails(VaultItem item) {
        insertMovie = fillMovieDetails(movieDetails, item, () -> playMovie(item));
    }

    private void showPhoneMovieDetails(VaultItem item) {
        LinearLayout details = panel();
        ScrollView scroll = new ScrollView(this);
        scroll.addView(details);
        AlertDialog dialog = new AlertDialog.Builder(this).setView(scroll)
                .setNegativeButton("Back to movies", null).create();
        fillMovieDetails(details, item, () -> {
            dialog.dismiss();
            playMovie(item);
        });
        dialog.show();
    }

    private TextView fillMovieDetails(LinearLayout details, VaultItem item, Runnable play) {
        details.removeAllViews();
        LinearLayout top = new LinearLayout(this);
        top.setGravity(Gravity.CENTER_VERTICAL);
        FrameLayout caseFrame = new FrameLayout(this);
        caseFrame.setPadding(dp(9), dp(4), dp(4), dp(6));
        caseFrame.setBackground(surface(new int[]{color("#98745F"), color("#23172E"), color("#130E21")},
                color("#B38D73"), 1, 5));
        ImageView cover = movieArtwork(item, false);
        cover.setScaleType(ImageView.ScaleType.FIT_CENTER);
        cover.setBackgroundColor(color("#0C0B1B"));
        caseFrame.addView(cover, new FrameLayout.LayoutParams(-1, -1));
        top.addView(caseFrame, new LinearLayout.LayoutParams(dp(87), dp(125)));
        LinearLayout labels = new LinearLayout(this);
        labels.setOrientation(LinearLayout.VERTICAL);
        labels.setPadding(dp(9), 0, 0, 0);
        TextView label = sectionTitle("SELECTED CASSETTE", "#63EFFF");
        label.setPadding(dp(4), 0, 0, dp(3));
        labels.addView(label);
        TextView title = text(item.title, 21, true);
        title.setTextColor(color("#FFF35E"));
        title.setPadding(dp(4), 0, 0, dp(5));
        labels.addView(title);
        if (!item.year.isEmpty()) labels.addView(text(item.year, 11, true));
        top.addView(labels, new LinearLayout.LayoutParams(0, -2, 1));
        details.addView(top);
        addGap(details, 8);
        TextView playMovie = focusButton("▶  INSERT MOVIE");
        playMovie.setContentDescription("Insert movie");
        if (phoneLayout) playMovie.setMinHeight(dp(48));
        playMovie.setGravity(Gravity.CENTER);
        playMovie.setTextColor(color("#061D17"));
        paintInsertMovie(playMovie, false);
        playMovie.setOnFocusChangeListener((button, focused) -> paintInsertMovie(button, focused));
        playMovie.setOnClickListener(v -> {
            playMenuSound();
            play.run();
        });
        details.addView(playMovie);
        if (!item.summary.isEmpty()) {
            TextView summary = text(item.summary, 12, false);
            summary.setPadding(dp(4), dp(5), dp(4), dp(4));
            details.addView(summary);
        }
        return playMovie;
    }

    private void paintInsertMovie(View button, boolean focused) {
        button.setBackground(surface(new int[]{color("#8AFFB8"), color("#27E882")},
                color(focused ? "#FFF5F9" : "#4FF093"), focused ? 3 : 1, 7));
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
        if (end < vaultItems.size() || nextMovieOffset >= 0) {
            boolean buffered = end < vaultItems.size();
            moreMovies = focusButton(buffered ? "MORE MOVIES" : "LOAD MORE MOVIES");
            moreMovies.setOnClickListener(v -> {
                if (buffered) growMovieShelf(creator);
                else loadMoviePage(creator, nextMovieOffset);
            });
            movieShelf.addView(moreMovies, new LinearLayout.LayoutParams(-1, -2));
        }
        if (moveFocus && start < movieShelf.getChildCount()) movieShelf.getChildAt(start).requestFocus();
    }

    private View vaultBox(VaultItem item) {
        return vaultBox(item, "CHANNELFORGE HOME VIDEO", view -> {
            if (phoneLayout) {
                showPhoneMovieDetails(item);
                return;
            }
            showMovieDetails(item);
            insertMovie.requestFocus();
        });
    }

    private View vaultBox(VaultItem item, String subtitle, View.OnClickListener select) {
        LinearLayout box = new LinearLayout(this);
        box.setGravity(Gravity.CENTER_VERTICAL);
        box.setPadding(dp(8), dp(4), dp(7), dp(4));
        box.setFocusable(true);
        box.setContentDescription(item.title + ", " + subtitle);
        paintTape(box, false);
        TextView brand = text("CF", 17, true);
        brand.setTypeface(Typeface.DEFAULT, Typeface.BOLD_ITALIC);
        brand.setPadding(0, 0, dp(7), 0);
        brand.setTextColor(color("#FCE0BC"));
        box.addView(brand);
        View stripe = new View(this);
        stripe.setBackgroundColor(color("#40CCEF"));
        stripe.setRotation(17f);
        LinearLayout.LayoutParams stripeLayout = new LinearLayout.LayoutParams(dp(4), dp(30));
        stripeLayout.rightMargin = dp(5);
        box.addView(stripe, stripeLayout);
        ImageView poster = movieArtwork(item, true);
        poster.setScaleType(ImageView.ScaleType.CENTER_CROP);
        poster.setBackgroundColor(color("#201528"));
        FrameLayout frame = new FrameLayout(this);
        frame.setPadding(dp(1), dp(1), dp(1), dp(1));
        frame.setBackgroundColor(color("#CE7BAD"));
        frame.addView(poster, new FrameLayout.LayoutParams(-1, -1));
        box.addView(frame, new LinearLayout.LayoutParams(dp(51), dp(32)));
        LinearLayout labels = new LinearLayout(this);
        labels.setOrientation(LinearLayout.VERTICAL);
        labels.setPadding(dp(7), 0, dp(4), 0);
        TextView title = text(item.title, 15, true);
        title.setTypeface(Typeface.DEFAULT, Typeface.BOLD_ITALIC);
        title.setSingleLine(true);
        title.setEllipsize(android.text.TextUtils.TruncateAt.END);
        title.setPadding(0, 0, 0, 0);
        labels.addView(title);
        TextView line = text(subtitle, 9, true);
        line.setSingleLine(true);
        line.setTextColor(color("#CFBDD5"));
        line.setLetterSpacing(.11f);
        line.setPadding(0, 0, 0, 0);
        labels.addView(line);
        box.addView(labels, new LinearLayout.LayoutParams(0, -2, 1));
        if (!item.year.isEmpty()) {
            TextView year = text(item.year, 11, true);
            year.setPadding(dp(3), dp(2), dp(3), dp(2));
            year.setRotation(-7f);
            year.setBackground(surface(new int[]{color("#B13932"), color("#7E282B")}, color("#E19177"), 1, 3));
            LinearLayout.LayoutParams yearLayout = new LinearLayout.LayoutParams(-2, -2);
            yearLayout.rightMargin = dp(7);
            box.addView(year, yearLayout);
        }
        TextView format = text("VHS", 10, true);
        format.setPadding(dp(3), dp(3), dp(3), dp(3));
        format.setTextColor(color("#F4D3A9"));
        format.setBackground(surface(new int[]{color("#221A25"), color("#171421")}, color("#DDB892"), 1, 3));
        box.addView(format);
        LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(-1, dp(phoneLayout ? 52 : 42));
        layout.setMargins(0, 0, 0, dp(5));
        box.setLayoutParams(layout);
        box.setOnFocusChangeListener((view, focused) -> {
            if (focused) playMenuSound();
            boolean motion = motionEnabled();
            view.animate().translationY(focused && motion ? -dp(2) : 0)
                    .setDuration(motion ? 160 : 0).start();
            view.setTranslationZ(focused ? dp(2) : 0);
            paintTape(view, focused);
        });
        box.setOnClickListener(view -> {
            playMenuSound();
            select.onClick(view);
        });
        return box;
    }

    private ImageView movieArtwork(VaultItem item, boolean thumbnail) {
        return new ImageView(this) {
            private final android.graphics.Rect visible = new android.graphics.Rect();
            private boolean requested;

            @Override protected void onDraw(android.graphics.Canvas canvas) {
                super.onDraw(canvas);
                if (requested || isDestroyed() || item.poster.isEmpty() || !getLocalVisibleRect(visible)) return;
                requested = true;
                Bitmap cached = posters.get(item.poster);
                if (cached != null) {
                    post(() -> { if (isAttachedToWindow()) setImageBitmap(cached); });
                } else if (thumbnail) {
                    posterLoader.execute(() -> loadPoster(item.poster, this));
                } else {
                    movieCoverLoader.execute(() -> updatePoster(moviePoster(item), this));
                }
            }
        };
    }

    private void paintTape(View row, boolean focused) {
        Drawable texture = getDrawable(R.drawable.cinema_spine);
        if (texture instanceof BitmapDrawable) ((BitmapDrawable) texture).setGravity(Gravity.FILL);
        GradientDrawable border = surface(new int[]{color("#2825213C"), color("#240B071E")},
                color(focused ? "#FF67E3" : "#B38D7A"), focused ? 2 : 1, 6);
        row.setBackground(new LayerDrawable(new Drawable[]{texture, border}));
    }

    private void loadPoster(String address, ImageView poster) {
        updatePoster(posterBitmap(address), poster);
    }

    private void updatePoster(Bitmap image, ImageView poster) {
        if (image != null) runOnUiThread(() -> {
            if (poster.isAttachedToWindow()) poster.setImageBitmap(image);
        });
    }

    private Bitmap moviePoster(VaultItem movie) {
        Bitmap image = posterBitmap(movie.poster);
        if (image == null && !movie.poster.equals(movie.fullPoster)) {
            image = posterBitmap(movie.fullPoster);
            if (image != null) posters.put(movie.poster, image);
        }
        return image;
    }

    private Bitmap posterBitmap(String address) {
        Bitmap cached = posters.get(address);
        if (cached != null) return cached;
        HttpURLConnection connection = null;
        try {
            URL url = new URL(address);
            if (!"https".equals(url.getProtocol())) return null;
            connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(6500);
            connection.setRequestProperty("User-Agent", "ChannelForge-TV/" + BuildConfig.VERSION_NAME);
            connection.setRequestProperty("Accept", "image/jpeg, image/png, image/webp, image/*");
            BitmapFactory.Options options = new BitmapFactory.Options();
            options.inSampleSize = 2;
            try (InputStream input = connection.getInputStream()) {
                Bitmap image = BitmapFactory.decodeStream(input, null, options);
                if (image != null) posters.put(address, image);
                return image;
            }
        } catch (Exception ignored) {
        } finally {
            if (connection != null) connection.disconnect();
        }
        return null;
    }

    private void selectUpdate() {
        if (BuildConfig.TESTER_BUILD) {
            showTesterNotes(testerVersionLabel(), BuildConfig.TESTER_RELEASE_NOTES,
                    "Open System → Check for Update to look for a newer build.");
            return;
        }
        if (updateBusy) return;
        if (latestUpdateNotes.isEmpty()) {
            checkUpdates(true);
        } else showUpdateNotes();
    }

    private void showTesterNotes(String label, String notes, String status) {
        new AlertDialog.Builder(this)
                .setTitle(label + " patch notes")
                .setMessage(notes.isEmpty() ? status : notes + "\n\n" + status)
                .setPositiveButton("Done", null)
                .show();
    }

    private void showUpdateNotes() {
        boolean available = pendingUpdate != null;
        AlertDialog.Builder dialog = new AlertDialog.Builder(this)
                .setTitle("What's new in v" + (available ? pendingUpdate.version : BuildConfig.VERSION_NAME))
                .setMessage(latestUpdateNotes.isEmpty() ? "Playback and reliability improvements." : latestUpdateNotes);
        dialog.setNeutralButton("Diagnostics", (box, which) -> showDiagnostics());
        if (available) dialog.setNegativeButton("Later", null)
                .setPositiveButton("Install update", (box, which) -> installSelectedUpdate());
        else dialog.setPositiveButton("Done", null);
        dialog.show();
    }

    private void installSelectedUpdate() {
        if (updateBusy || pendingUpdate == null) return;
        if (canInstallUpdates()) {
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
        if (requestCode == EXPORT_REPORT) {
            String report = pendingReport;
            pendingReport = null;
            if (resultCode == RESULT_OK && data != null && data.getData() != null && report != null) {
                try (OutputStream out = getContentResolver().openOutputStream(data.getData(), "wt")) {
                    if (out == null) throw new IOException("No output stream");
                    out.write(report.getBytes(StandardCharsets.UTF_8));
                    Toast.makeText(this, "Playback report saved.", Toast.LENGTH_SHORT).show();
                } catch (IOException | SecurityException e) {
                    Toast.makeText(this, "Could not write the playback report.", Toast.LENGTH_LONG).show();
                }
            }
            return;
        }
        if (requestCode != INSTALL_SETTINGS_REQUEST || pendingUpdate == null || updateBusy) return;
        if (canInstallUpdates()) downloadUpdate();
        else showBanner("Allow ChannelForge TV to install unknown apps, then select Updates again.");
    }

    private void checkUpdates(boolean requested) {
        if (updateBusy) return;
        updateBusy = true;
        updateButton.setText("UPDATES  •  CHECKING…");
        updateLoader.execute(() -> {
            try {
                StringBuilder notes = new StringBuilder();
                FireTvUpdate release = FireTvUpdate.latest(notes);
                runOnUiThread(() -> {
                    if (isDestroyed()) return;
                    pendingUpdate = release;
                    latestUpdateNotes = notes.toString();
                    finishUpdate("");
                    if (requested && BuildConfig.TESTER_BUILD) {
                        if (release == null) {
                            showTesterNotes(testerVersionLabel(), BuildConfig.TESTER_RELEASE_NOTES,
                                    "You’re on the latest Tester build.");
                        } else {
                            if (canInstallUpdates()) {
                                showTesterNotes(alphaVersionLabel(release.version), latestUpdateNotes, "Downloading the update…");
                                downloadUpdate();
                            } else {
                                new AlertDialog.Builder(this)
                                        .setTitle(alphaVersionLabel(release.version) + " patch notes")
                                        .setMessage(latestUpdateNotes)
                                        .setNegativeButton("Later", null)
                                        .setPositiveButton("Install update", (dialog, which) -> installSelectedUpdate())
                                        .show();
                            }
                        }
                    } else if (requested) showUpdateNotes();
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    if (isDestroyed()) return;
                    updateBusy = false;
                    updateButton.setText(BuildConfig.TESTER_BUILD ? testerVersionLabel() : "UPDATES  •  RETRY");
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
            if (BuildConfig.TESTER_BUILD && pendingUpdate != null && !getSharedPreferences(PREFS, MODE_PRIVATE)
                    .edit().putInt(TesterUpdateReceiver.REOPEN_AFTER_UPDATE, pendingUpdate.testerRevision).commit()) {
                throw new IOException("Could not save the update restart state.");
            }
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
        if (BuildConfig.TESTER_BUILD && !message.isEmpty() && pendingUpdate != null) {
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().remove(TesterUpdateReceiver.REOPEN_AFTER_UPDATE).apply();
        }
        updateBusy = sessionCommitted = installerOpened = false;
        updateSession = -1;
        installConfirmation = null;
        updateButton.setText(BuildConfig.TESTER_BUILD ? testerVersionLabel()
                : pendingUpdate == null ? (phonePortrait ? "v" : "UP TO DATE ✓  v") + BuildConfig.VERSION_NAME
                : (phonePortrait ? "UPDATE\nv" : "UPDATE TO v") + pendingUpdate.version);
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
        long previousEnd = tunedProgramEnd;
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
                if (previousEnd != end) {
                    upcomingDue = false;
                    upcomingShown = false;
                }
                video.postDelayed(liveProgramBoundary, end - now + 1700);
                if (i + 1 < tunedChannel.programs.length()) {
                    hasUpcomingProgram = true;
                    long lead = Math.min(45_000L, (end - start) / 3);
                    if (!upcomingShown) video.postDelayed(upcomingProgram, Math.max(100L, end - now - lead));
                }
                return;
            }
        }
    }

    private void showUpcomingIfPlaying() {
        if (!upcomingDue || upcomingShown || !hasUpcomingProgram || guideVisible
                || tunedChannel == null || !renderedFrame || !player.isPlaying()) return;
        String next = currentAndNext(tunedChannel)[1];
        if (next.isEmpty()) return;
        upcomingShown = true;
        transitionAt = tunedProgramEnd;
        transitionTitle = next;
        showBanner("COMING UP NEXT\n" + next, tunedChannel.logo);
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
        video.removeCallbacks(resetPlaybackRetries);
        if (playbackWaiting) return;
        playbackWaiting = true;
        long timeout = tunedChannel == null ? 24_000L : (liveRetries == 0 ? 12_000L : 16_000L);
        video.postDelayed(playbackDeadline, timeout);
    }

    private void clearPlaybackDeadline() {
        playbackWaiting = false;
        video.removeCallbacks(playbackDeadline);
    }

    private long finishRebuffer() {
        video.removeCallbacks(lowerQuality);
        long stalledFor = rebufferStarted == 0 ? 0 : SystemClock.elapsedRealtime() - rebufferStarted;
        totalRebufferMs += stalledFor;
        rebufferStarted = 0;
        return stalledFor;
    }

    private boolean liveStallsRepeated() {
        long now = SystemClock.elapsedRealtime();
        while (!recentLiveStalls.isEmpty() && now - recentLiveStalls.peekFirst() >= 30_000) recentLiveStalls.removeFirst();
        recentLiveStalls.addLast(now);
        return recentLiveStalls.size() >= 3;
    }

    private void setBitrateCap(int value) {
        if (temporaryBitrateCap == value) return;
        temporaryBitrateCap = value;
        player.setTrackSelectionParameters(player.getTrackSelectionParameters().buildUpon()
                .setMaxVideoBitrate(value).build());
    }

    private void lowerQualityDuringStall() {
        Format playing = player.getVideoFormat();
        if (playing == null || rebufferStarted == 0 || guideVisible || recovering || !player.getPlayWhenReady()) return;
        int limit = Math.min(playing.bitrate, temporaryBitrateCap);
        int lower = 0;
        for (Tracks.Group group : player.getCurrentTracks().getGroups()) {
            if (group.getType() != C.TRACK_TYPE_VIDEO) continue;
            for (int i = 0; i < group.length; i++) {
                int rate = group.getTrackFormat(i).bitrate;
                if (group.isTrackSupported(i) && rate > lower && rate < limit) lower = rate;
            }
        }
        if (lower > 0) setBitrateCap(lower);
    }

    private void hidePlaybackBuffer() {
        clearPlaybackDeadline();
        bufferPanel.setVisibility(View.GONE);
        if (player.isPlaying()) {
            video.removeCallbacks(resetPlaybackRetries);
            video.postDelayed(resetPlaybackRetries, 30000);
        }
    }

    private static boolean standardHttps(Uri address) {
        return "https".equalsIgnoreCase(address.getScheme()) && address.getUserInfo() == null
                && (address.getPort() == -1 || address.getPort() == 443)
                && address.getHost() != null
                && address.getHost().matches("[a-zA-Z0-9-]+(?:\\.[a-zA-Z0-9-]+)*\\.[a-zA-Z][a-zA-Z0-9-]*");
    }

    private Uri preferredPlayback(Uri fallback, boolean live) {
        if (skipPreferredRelay || !standardHttps(fallback)) return fallback;
        Creator source = live && tunedChannel != null ? studioFor(tunedChannel)
                : activeMovie == null ? null : studioById(activeMovie.studioId);
        if (source == null) return fallback;
        Uri registered = Uri.parse(source.playlist);
        if (!standardHttps(registered) || !fallback.getHost().equalsIgnoreCase(registered.getHost())) return fallback;
        String preferred = source.playbackOrigin;
        if (!BuildConfig.PLAYBACK_RELAY_ROUTE.isEmpty()) {
            String[] route = BuildConfig.PLAYBACK_RELAY_ROUTE.split("=", 2);
            if (route[0].equalsIgnoreCase(registered.getHost())) preferred = "https://" + route[1];
        }
        Uri relay = Uri.parse(preferred);
        if (!standardHttps(relay) || relay.getQuery() != null || relay.getFragment() != null
                || !(relay.getPath() == null || relay.getPath().isEmpty() || "/".equals(relay.getPath()))
                || relay.getHost().equalsIgnoreCase(fallback.getHost())) return fallback;
        return fallback.buildUpon().encodedAuthority(relay.getEncodedAuthority()).build();
    }

    private void startStream(String url, boolean live) {
        renderedFrame = false;
        recentLiveStalls.clear();
        lastFrameChange = SystemClock.elapsedRealtime();
        lastFrameCount = -1;
        video.removeCallbacks(watchdog);
        video.postDelayed(watchdog, 2000);
        video.removeCallbacks(resetPlaybackRetries);
        Uri fallback = Uri.parse(url);
        Uri input = preferredPlayback(fallback, live);
        String host = input.getHost();
        deliveryPath = host == null ? "unknown" : host.equals("channelforge-vps.tailb58ce4.ts.net") ? "managed-vps" :
                host.endsWith(".lhr.life") ? "legacy-tunnel" :
                host.equals("localhost") || host.equals("127.0.0.1") || host.equals("[::1]") ? "local" : "direct";
        usingPreferredRelay = !input.equals(fallback);
        if (usingPreferredRelay) android.util.Log.i("ChannelForgePlayback", "Using preferred media relay");
        String path = input.getPath();
        if (path != null) path = STUDIO_SLOT_PATH.matcher(path).replaceFirst("");
        if (path != null && (path.startsWith("/broadcast/") || path.startsWith("/live/") || path.startsWith("/vault/"))) {
            input = input.buildUpon().appendQueryParameter("viewer", viewerLease).build();
        }
        MediaItem.Builder media = new MediaItem.Builder().setUri(input).setMediaId(viewerLease).setMimeType(MimeTypes.APPLICATION_M3U8);
        if (live) {
            media.setLiveConfiguration(new MediaItem.LiveConfiguration.Builder().setTargetOffsetMs(20000).build());
            player.setMediaItem(media.build());
        } else {
            player.setMediaItem(media.build(), resumePosition);
        }
        player.setPlayWhenReady(live);
        player.prepare();
    }

    private void beginTune() {
        ++playGeneration;
        viewerLease = UUID.randomUUID().toString().replace("-", "");
        recentEvents.clear();
        recovering = false;
        usingPreferredRelay = skipPreferredRelay = false;
        finishRebuffer();
        totalRebufferMs = rebufferCount = 0;
        setBitrateCap(Integer.MAX_VALUE);
        totalRetries = liveRetries = movieRetries = lastErrorCode = lastHttpCode = 0;
        routeRecoveryStarted = 0;
        lastHttpResource = null;
        segmentLoads = segmentErrors = manifestErrors = 0;
        segmentLoadMs = segmentLoadBytes = slowestSegmentMs = slowestManifestMs = 0;
        relayCacheHits = relayCacheMisses = 0;
        slowestRelayUpstreamMs = slowestStudioResponseMs = -1;
        resumePosition = 0;
        lastReason = "none";
        tuneStarted = SystemClock.elapsedRealtime();
        firstFrameMs = -1;
        testerEvent("load");
    }

    private static String responseHeader(Map<String, List<String>> headers, String name) {
        for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
            if (name.equalsIgnoreCase(entry.getKey()) && entry.getValue() != null && !entry.getValue().isEmpty())
                return entry.getValue().get(0);
        }
        return "";
    }

    private void recordDeliveryHeaders(Map<String, List<String>> headers) {
        if ("vps".equals(responseHeader(headers, "X-ChannelForge-Relay"))) deliveryPath = "managed-vps";
        String cache = responseHeader(headers, "X-ChannelForge-Cache");
        if ("HIT".equals(cache) || "STALE".equals(cache) || "UPDATING".equals(cache) || "REVALIDATED".equals(cache)) {
            relayCacheHits++;
            return; // Cached headers describe an earlier Studio fetch.
        }
        if ("MISS".equals(cache) || "EXPIRED".equals(cache) || "BYPASS".equals(cache)) relayCacheMisses++;
        String upstream = responseHeader(headers, "X-ChannelForge-Upstream-Header-Time");
        String studio = responseHeader(headers, "X-ChannelForge-Studio-Response-Ms");
        if (upstream.matches("[0-9]{1,3}\\.[0-9]{3}"))
            slowestRelayUpstreamMs = Math.max(slowestRelayUpstreamMs, Math.round(Double.parseDouble(upstream) * 1000));
        if (studio.matches("[0-9]{1,6}"))
            slowestStudioResponseMs = Math.max(slowestStudioResponseMs, Long.parseLong(studio));
    }

    private void recover(String reason) {
        recover(reason, false);
    }

    private void recover(String reason, boolean deadlineExpired) {
        if (guideVisible || ejectingTape || playbackFailed || recovering) return;
        recovering = true;
        finishRebuffer();
        clearPlaybackDeadline();
        video.removeCallbacks(continueLive);
        video.removeCallbacks(resetPlaybackRetries);
        video.removeCallbacks(watchdog);
        video.removeCallbacks(liveProgramBoundary);
        video.removeCallbacks(upcomingProgram);
        lastReason = reason;
        if (recentEvents.size() == 16) recentEvents.removeFirst();
        recentEvents.addLast(System.currentTimeMillis() + " " + reason + " (HTTP " + lastHttpCode + ")");
        testerEvent("retry");
        if (activeMovie != null && (renderedFrame || player.getCurrentPosition() > 0))
            resumePosition = Math.max(0, player.getCurrentPosition());
        int generation = ++playGeneration;
        if (activeMovie != null) clearCassette();
        player.stop();
        final Channel channel = tunedChannel;
        final VaultItem film = activeMovie;
        if (usingPreferredRelay) {
            usingPreferredRelay = false;
            skipPreferredRelay = true;
            totalRetries++;
            android.util.Log.i("ChannelForgePlayback", "Preferred relay unavailable; using registered media route");
            resumeAfterRecovery(channel, film);
            return;
        }
        int attempt = channel != null ? ++liveRetries : ++movieRetries;
        if (attempt == 1) routeRecoveryStarted = SystemClock.elapsedRealtime();
        final Creator source = channel == null && film != null && !film.studioId.isEmpty()
                ? studioById(film.studioId) : channel == null ? currentStudio : studioById(channel.studioId);
        final String studioId = source != null ? source.id : film != null ? film.studioId : channel != null ? channel.studioId : "";
        boolean replacementOnly = BuildConfig.TESTER_BUILD && attempt == 4 && !studioId.isEmpty()
                && (channel != null && !channel.id.isEmpty() || film != null && !film.id.isEmpty());
        if (attempt > 3 && !replacementOnly) {
            failRecovery(reason);
            return;
        }
        totalRetries++;
        showBuffering(replacementOnly ? "Checking for the Studio’s backup connection…"
                : reason + ". Reconnecting (" + attempt + "/3)…", true);
        boolean startupLookup = BuildConfig.TESTER_BUILD && deadlineExpired && channel != null
                && attempt == 1 && segmentLoads == 0 && !renderedFrame;
        boolean shortGuide = startupLookup || replacementOnly;
        boolean refreshRoute = startupLookup || attempt > 1 || lastHttpCode == 404 || lastHttpCode == 410
                || lastHttpCode == 502 || lastHttpCode == 504;
        video.postDelayed(() -> {
            if (generation != playGeneration || guideVisible || isDestroyed()) return;
            if (!refreshRoute || (source == null && studioId.isEmpty())) {
                resumeAfterRecovery(channel, film);
                return;
            }
            // Retry the known route if catalog I/O or queued studio work takes too long.
            Runnable routeDeadline = () -> {
                if (generation == playGeneration && !guideVisible && !isDestroyed()) {
                    ++playGeneration;
                    if (replacementOnly) failRecovery(reason);
                    else resumeAfterRecovery(channel, film);
                }
            };
            video.postDelayed(routeDeadline, 8000);
            recoveryLoader.execute(() -> {
                String updatedUrl = "";
                Channel fresh = null;
                try {
                    Creator origin = source;
                    try {
                        for (Creator entry : parseCreators(new JSONObject(get(BuildConfig.DIRECTORY_URL)))) {
                            if (studioId.equals(entry.id)) { origin = entry; break; }
                        }
                    } catch (Exception unavailable) {
                        android.util.Log.w("ChannelForgePlayback", "Directory unavailable during route refresh");
                    }
                    if (channel != null && origin != null && !origin.channelStatus.isEmpty()) {
                        Uri status = Uri.parse(origin.channelStatus);
                        Uri previous = Uri.parse(channel.guideUrl).buildUpon().clearQuery().fragment(null).build();
                        // A first startup timeout only needs a short guide from a replacement origin.
                        if (!shortGuide || !status.buildUpon().clearQuery().fragment(null).build().equals(previous)) {
                            String address = status.buildUpon().appendQueryParameter("guide", shortGuide ? "brief" : "extended").build().toString();
                            for (Channel row : parseOnlineChannels(get(address), address)) {
                                if ((!channel.id.isEmpty() && channel.id.equals(row.id)) || channel.url.equals(row.url)) {
                                    row.guideUrl = status.buildUpon().appendQueryParameter("guide", "extended").build().toString();
                                    fresh = row;
                                    break;
                                }
                            }
                        }
                    } else if (film != null && origin != null && !origin.vault.isEmpty()) {
                        Uri vault = Uri.parse(origin.vault);
                        String path = vault.getPath(), suffix = "/api/public/vault";
                        if ("https".equals(vault.getScheme()) && path != null && path.endsWith(suffix)
                                && film.id.matches("[a-f0-9]{24}")) {
                            updatedUrl = vault.buildUpon().path(path.substring(0, path.length() - suffix.length())
                                    + "/vault/" + film.id + ".m3u8").clearQuery().fragment(null).build().toString();
                        } else {
                            String address = vault.buildUpon().appendQueryParameter("q", film.title)
                                    .appendQueryParameter("limit", "50").build().toString();
                            for (VaultItem row : parseVault(new JSONObject(get(address)))) {
                                if ((!film.id.isEmpty() && film.id.equals(row.id)) || row.releaseKey().equals(film.releaseKey())) {
                                    updatedUrl = row.url; break;
                                }
                            }
                        }
                    }
                } catch (Exception unavailable) {
                    android.util.Log.w("ChannelForgePlayback", "Route refresh failed: " + unavailable.getClass().getSimpleName());
                }
                final Channel replacement = fresh;
                final String movieUrl = updatedUrl;
                runOnUiThread(() -> {
                    video.removeCallbacks(routeDeadline);
                    if (generation != playGeneration || guideVisible || isDestroyed()) return;
                    if (replacementOnly && replacement == null && (movieUrl.isEmpty() || film == null || movieUrl.equals(film.url))) {
                        failRecovery(reason);
                        return;
                    }
                    if (channel != null) {
                        if (replacement != null) {
                            channel.url = replacement.url;
                            channel.continuousUrl = replacement.continuousUrl;
                            channel.fireTvUrl = replacement.fireTvUrl;
                            if (!shortGuide || channel.programs == null) channel.programs = replacement.programs;
                            channel.guideUrl = replacement.guideUrl;
                            channel.state = replacement.state;
                            channel.airUntil = replacement.airUntil;
                        }
                    } else if (film != null) {
                        if (!movieUrl.isEmpty()) film.url = movieUrl;
                    }
                    resumeAfterRecovery(channel, film);
                });
            });
        }, replacementOnly ? Math.max(750, 30_000 - (SystemClock.elapsedRealtime() - routeRecoveryStarted)) : 750L << (attempt - 1));
    }

    private void failRecovery(String reason) {
        recovering = false;
        playbackFailed = true;
        showBuffering(phoneLayout ? reason + "." : reason + ". Press OK to retry or Back for the guide.", false);
    }

    private void resumeAfterRecovery(Channel channel, VaultItem film) {
        recovering = false;
        if (channel != null) {
            continueLive.run();
        } else if (film != null) {
            waitForPlayback();
            startStream(film.url, false);
            if (cassetteMotion == null) player.play();
        }
    }

    private Creator studioFor(Channel channel) {
        return studioById(channel.studioId);
    }

    private Creator studioById(String id) {
        for (Creator entry : creators) {
            if (id.equals(entry.id)) return entry;
        }
        return null;
    }

    private void play(int index) {
        if (channels.isEmpty()) return;
        play(channels.get((index + channels.size()) % channels.size()));
    }

    private void play(Channel channel) {
        int index = channels.indexOf(channel);
        if (index >= 0) channelIndex = index;
        beginTune();
        tunedChannel = channel;
        tunedProgramEnd = 0;
        upcomingDue = upcomingShown = false;
        transitionAt = 0;
        streamTitle = tunedChannel.name;
        liveRetries = 0;
        clearPlaybackDeadline();
        activeMovie = null;
        guide.setVisibility(View.GONE);
        guideVisible = false;
        if (touchGuideButton != null) touchGuideButton.setVisibility(View.VISIBLE);
        video.setVisibility(View.VISIBLE);
        video.hideController();
        video.setUseController(false);
        video.setFocusable(true);
        video.requestFocus();
        video.removeCallbacks(continueLive);
        continueLive.run();
        showChannelBanner(tunedChannel);
    }

    private void playMovie(VaultItem movie) {
        playMovie(movie, 0);
    }

    private void playMovie(VaultItem movie, long position) {
        beginTune();
        tunedChannel = null;
        video.removeCallbacks(upcomingProgram);
        video.removeCallbacks(liveProgramBoundary);
        streamTitle = movie.title;
        activeMovie = movie;
        movieFinished = false;
        resumePosition = position;
        movieRetries = 0;
        clearPlaybackDeadline();
        video.removeCallbacks(continueLive);
        guide.setVisibility(View.GONE);
        guideVisible = false;
        video.setVisibility(View.VISIBLE);
        video.setUseController(true);
        video.setFocusable(true);
        video.requestFocus();
        showBuffering("Connecting to the studio…", true);
        waitForPlayback();
        startStream(movie.url, false);
        int generation = playGeneration;
        Runnable startMovie = () -> {
            if (playGeneration == generation && !ejectingTape && activeMovie == movie) player.play();
        };
        animateCassette(true, movie, startMovie);
        // Playback must not depend on the animation reporting that it finished.
        video.postDelayed(() -> {
            if (playGeneration != generation || ejectingTape || activeMovie != movie) return;
            clearCassette();
            if (!player.getPlayWhenReady()) startMovie.run();
        }, 3500);
        showBanner("REWIND CINEMA   •   " + movie.title);
    }
    private String displayNumber(Channel channel, int index) {
        return channel.number.isEmpty() ? String.format(Locale.US, "%03d", index + 1) : channel.number;
    }

    private void showChannelBanner(Channel channel) {
        String message = "CH " + displayNumber(channel, channelIndex) + "   " + channel.name;
        String[] titles = currentAndNext(channel);
        if (!titles[0].isEmpty()) message += "\nNOW  " + titles[0];
        if (!titles[1].isEmpty()) message += "\nCOMING UP NEXT  " + titles[1];
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
        if (loading && tunedChannel != null && Math.abs(System.currentTimeMillis() - transitionAt) < 12000) {
            message = "COMING UP NEXT\n" + transitionTitle;
        }
        bufferLabel.setText(message + (streamTitle.isEmpty() ? "" : "\n" + streamTitle));
        bufferSpinner.setVisibility(loading ? View.VISIBLE : View.GONE);
        if (touchRetry != null) touchRetry.setVisibility(!loading && playbackFailed ? View.VISIBLE : View.GONE);
        if (touchDebugger != null) touchDebugger.setVisibility(!loading && playbackFailed ? View.VISIBLE : View.GONE);
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

    private void retrySelection() {
        if (!playbackFailed) return;
        if (tunedChannel != null) play(tunedChannel);
        else if (activeMovie != null) {
            movieRetries = 0;
            playbackFailed = false;
            recover("Retrying movie");
        }
    }

    @Override public boolean dispatchKeyEvent(KeyEvent event) {
        int key = event.getKeyCode();
        boolean select = key == KeyEvent.KEYCODE_DPAD_CENTER || key == KeyEvent.KEYCODE_ENTER;
        if (!guideVisible && select && (playbackFailed || consumingRetryKey)) {
            if (event.getAction() == KeyEvent.ACTION_DOWN && event.getRepeatCount() == 0 && playbackFailed) {
                consumingRetryKey = true;
                retrySelection();
            } else if (event.getAction() == KeyEvent.ACTION_UP) consumingRetryKey = false;
            return true;
        }
        if (!guideVisible && activeMovie != null
                && (key == KeyEvent.KEYCODE_BACK || key == KeyEvent.KEYCODE_BUTTON_B)) {
            if (event.getAction() == KeyEvent.ACTION_DOWN && event.getRepeatCount() == 0) ejectMovie();
            return true;
        }
        return super.dispatchKeyEvent(event);
    }

    private boolean testerConsented() {
        return getSharedPreferences(PREFS, MODE_PRIVATE).getBoolean(TESTER_CONSENT, false);
    }

    private void testerEvent(String kind) {
        if (!BuildConfig.TESTER_BUILD || !testerConsented()) return;
        try {
            if (testerTimeline.size() == 40) testerTimeline.removeFirst();
            testerTimeline.addLast(new JSONObject().put("kind", kind).put("ms", SystemClock.elapsedRealtime()));
            ((TesterApplication) getApplication()).rememberTimeline(new JSONArray(testerTimeline));
        } catch (JSONException invalid) { throw new IllegalStateException(invalid); }
    }

    private void showTesterTools() {
        if (!testerConsented()) return;
        TesterApplication tester = (TesterApplication) getApplication();
        String current = tester.automaticReports() ? "Enabled" : "Off";
        new AlertDialog.Builder(this).setTitle("Developer").setItems(
                new String[]{"Review playback report", "Review saved crash report", "Automatic crash reports · " + current, "Stop testing"},
                (dialog, selection) -> {
                    if (selection == 0) showTesterReport();
                    else if (selection == 1) showSavedCrash(tester);
                    else if (selection == 2) new AlertDialog.Builder(this).setTitle("Automatic crash reports")
                            .setMessage("After an app crash, send its error type, build, device model, and recent playback events when you reopen ChannelForge. You can turn this off here.")
                            .setNegativeButton("Close", null)
                            .setPositiveButton(tester.automaticReports() ? "Turn off" : "Enable",
                                    (box, button) -> tester.setAutomaticReports(!tester.automaticReports())).show();
                    else {
                        tester.setAutomaticReports(false);
                        tester.clearSavedCrash();
                        getSharedPreferences(PREFS, MODE_PRIVATE).edit().remove(TESTER_CONSENT).apply();
                        testerTimeline.clear();
                        finish();
                    }
                }).setNegativeButton("Close", null).show();
    }

    private void showSavedCrash(TesterApplication tester) {
        String report = tester.savedCrash();
        if (report == null) {
            new AlertDialog.Builder(this).setTitle("Crash reports")
                    .setMessage("There is no saved app crash report. Send a playback report for a channel that won’t load.")
                    .setNegativeButton("Close", null).setPositiveButton("Playback report", (dialog, which) -> showTesterReport())
                    .show();
            return;
        }
        AlertDialog review = new AlertDialog.Builder(this).setTitle("Saved crash report").setMessage(report)
                .setNegativeButton("Close", null).setPositiveButton("Send crash report", null).show();
        Button send = review.getButton(AlertDialog.BUTTON_POSITIVE);
        send.setOnClickListener(view -> deliverTesterReport(report, review, send, () -> tester.sendSavedCrash(report)));
    }

    private JSONObject snapshotForSharing() throws JSONException {
        JSONObject snapshot = diagnosticSnapshot();
        JSONObject shared = new JSONObject();
        for (String key : ("app engine sdk model trace_id directoryCached studioCount staleGuides firstFrameMs "
                + "bufferMs positionMs playing bandwidthEstimateBps retries temporaryBitrateCapBps "
                + "postStartRebuffers postStartRebufferMs playerErrorCode httpError http_resource build_revision width height "
                + "videoBitrateBps videoMime audioMime audioChannels renderedFrames droppedFrames "
                + "delivery_route delivery_path segment_loads segment_errors manifest_errors slowest_segment_ms slowest_manifest_ms segment_rate_bps "
                + "relay_cache_hits relay_cache_misses slowest_relay_upstream_ms slowest_studio_response_ms").split(" ")) {
            if (snapshot.has(key)) shared.put(key, snapshot.get(key));
        }
        shared.put("platform", TesterApplication.platform())
                .put("kind", activeMovie != null ? "movie" : tunedChannel != null ? "live" : JSONObject.NULL)
                .put("timeline", new JSONArray(testerTimeline));
        return shared;
    }

    private void showTesterReport() {
        try {
            JSONObject shared = guideVisible && lastPlaybackReport != null ? lastPlaybackReport : snapshotForSharing();
            AlertDialog.Builder tools = new AlertDialog.Builder(this).setTitle("Developer")
                    .setMessage(TESTER_NOTICE + "\n\n" + shared.toString(2))
                    .setNegativeButton("Close", null);
            if (!BuildConfig.TESTER_REPORT_URL.isEmpty()) {
                tools.setPositiveButton("Send report", null);
            }
            AlertDialog review = tools.show();
            Button send = review.getButton(AlertDialog.BUTTON_POSITIVE);
            if (send != null) send.setOnClickListener(view -> sendTesterReport(shared.toString(), review, send));
        } catch (JSONException invalid) { throw new IllegalStateException(invalid); }
    }

    private void sendTesterReport(String report, AlertDialog dialog, Button button) {
        deliverTesterReport(report, dialog, button, () -> TesterApplication.post(report.getBytes(StandardCharsets.UTF_8)));
    }

    private interface ReportSender { String send(); }

    private void deliverTesterReport(String report, AlertDialog dialog, Button button, ReportSender sender) {
        if (!testerConsented()) return;
        button.setEnabled(false);
        button.setText("Sending…");
        updateLoader.execute(() -> {
            String receipt = sender.send();
            String result = receipt == null ? "Could not send the report. The testing link may have expired."
                    : "Report sent. ID " + receipt;
            runOnUiThread(() -> {
                if (dialog.isShowing()) {
                    dialog.setMessage(result + "\n\n" + report);
                    button.setText(receipt == null ? "Retry report" : "Sent ✓");
                    button.setEnabled(receipt == null);
                    if (receipt != null) dialog.getButton(AlertDialog.BUTTON_NEGATIVE).requestFocus();
                } else Toast.makeText(this, result, Toast.LENGTH_LONG).show();
            });
        });
    }

    private JSONObject diagnosticSnapshot() {
        JSONObject result = new JSONObject();
        try {
            result.put("app", BuildConfig.VERSION_NAME).put("engine", "Media3 ExoPlayer").put("trace_id", viewerLease)
                    .put("sdk", Build.VERSION.SDK_INT).put("model", Build.MODEL)
                    .put("directoryLastChecked", directoryChecked).put("directoryCached", directoryStale)
                    .put("studioCount", creators.size()).put("staleGuides", staleGuides.size())
                    .put("firstFrameMs", firstFrameMs).put("bufferMs", player.getTotalBufferedDuration())
                    .put("positionMs", player.getCurrentPosition()).put("playing", player.isPlaying())
                    .put("bandwidthEstimateBps", bandwidth.getBitrateEstimate()).put("retries", totalRetries)
                    .put("temporaryBitrateCapBps", temporaryBitrateCap == Integer.MAX_VALUE ? -1 : temporaryBitrateCap)
                    .put("postStartRebuffers", rebufferCount)
                    .put("postStartRebufferMs", totalRebufferMs + (rebufferStarted == 0 ? 0 : SystemClock.elapsedRealtime() - rebufferStarted))
                    .put("error", lastReason).put("playerErrorCode", lastErrorCode).put("httpError", lastHttpCode)
                    .put("http_resource", lastHttpResource).put("build_revision", BuildConfig.VERSION_CODE)
                    .put("segment_loads", segmentLoads).put("segment_errors", segmentErrors)
                    .put("manifest_errors", manifestErrors).put("slowest_segment_ms", slowestSegmentMs)
                    .put("slowest_manifest_ms", slowestManifestMs)
                    .put("segment_rate_bps", segmentLoadMs == 0 ? -1 : Math.round(8000.0 * segmentLoadBytes / segmentLoadMs))
                    .put("delivery_path", deliveryPath).put("relay_cache_hits", relayCacheHits).put("relay_cache_misses", relayCacheMisses)
                    .put("slowest_relay_upstream_ms", slowestRelayUpstreamMs).put("slowest_studio_response_ms", slowestStudioResponseMs)
                    .put("decoder", decoderName).put("processCpuPercentOfOneCore", cpuPercent)
                    .put("pssKiB", android.os.Debug.getPss()).put("events", new JSONArray(recentEvents));
            if (tunedChannel != null || activeMovie != null) {
                String route = usingPreferredRelay ? "preferred-relay" : skipPreferredRelay ? "registered-fallback" : "registered-route";
                result.put("delivery_route", route);
            }
            Creator source = tunedChannel == null ? currentStudio : studioFor(tunedChannel);
            if (source != null) result.put("studioState", source.state).put("studioLastHeartbeat", source.lastHeartbeat);
            if (tunedChannel != null) {
                String[] upcoming = currentAndNext(tunedChannel);
                result.put("channelState", tunedChannel.state).put("guideHasCurrentProgram", !upcoming[0].isEmpty())
                        .put("guideHasNextProgram", !upcoming[1].isEmpty()).put("currentProgramEndsMs", tunedProgramEnd);
            }
            androidx.media3.common.Format v = player.getVideoFormat(), a = player.getAudioFormat();
            if (v != null) result.put("videoMime", v.sampleMimeType).put("width", v.width)
                    .put("height", v.height).put("videoBitrateBps", v.bitrate);
            if (a != null) result.put("audioMime", a.sampleMimeType).put("audioChannels", a.channelCount);
            androidx.media3.exoplayer.DecoderCounters counters = player.getVideoDecoderCounters();
            if (counters != null) {
                counters.ensureUpdated();
                result.put("renderedFrames", counters.renderedOutputBufferCount).put("droppedFrames", counters.droppedBufferCount);
            }
        } catch (JSONException invalid) { throw new IllegalStateException(invalid); }
        return result;
    }

    private void showDiagnostics() {
        JSONObject snapshot = guideVisible && lastPublicPlayback != null ? lastPublicPlayback : diagnosticSnapshot();
        String report;
        try { report = snapshot.toString(2); } catch (JSONException e) { return; }
        new AlertDialog.Builder(this).setTitle("Playback diagnostics").setMessage(
                (tunedChannel == null ? "" : tunedChannel.name + "\n" + currentAndNext(tunedChannel)[0] + "\n\n") + report)
                .setPositiveButton("Close", null)
                .setNeutralButton("Export report", (dialog, button) -> {
                    pendingReport = report;
                    Intent save = new Intent(Intent.ACTION_CREATE_DOCUMENT).setType("application/json")
                            .addCategory(Intent.CATEGORY_OPENABLE).putExtra(Intent.EXTRA_TITLE, "ChannelForge-playback.json");
                    try { startActivityForResult(save, EXPORT_REPORT); }
                    catch (ActivityNotFoundException unavailable) {
                        pendingReport = null;
                        File folder = getExternalFilesDir(android.os.Environment.DIRECTORY_DOWNLOADS);
                        if (folder == null) { Toast.makeText(this, "Downloads unavailable.", Toast.LENGTH_LONG).show(); return; }
                        File file = new File(folder, "ChannelForge-playback.json");
                        try (FileOutputStream out = new FileOutputStream(file)) {
                            out.write(report.getBytes(StandardCharsets.UTF_8));
                            Toast.makeText(this, "Report saved in " + file, Toast.LENGTH_LONG).show();
                        } catch (IOException e) { Toast.makeText(this, "Report export failed.", Toast.LENGTH_LONG).show(); }
                    }
                }).show();
    }

    @Override public boolean onKeyDown(int key, KeyEvent event) {
        if (key == KeyEvent.KEYCODE_MENU) {
            if (event.getRepeatCount() == 0) {
                if (BuildConfig.TESTER_BUILD) showTesterTools();
                else showDiagnostics();
            }
            return true;
        }
        if (!guideVisible) {
            boolean select = key == KeyEvent.KEYCODE_DPAD_CENTER || key == KeyEvent.KEYCODE_ENTER;
            if (key == KeyEvent.KEYCODE_BACK || key == KeyEvent.KEYCODE_BUTTON_B || (select && tunedChannel != null)) {
                if (activeMovie != null) ejectMovie();
                else {
                    returnToGuide();
                    channelRail.requestFocus();
                }
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

    private void updatePlaybackScreenAwake() {
        if (!phoneLayout || video == null || player == null) return;
        int state = player.getPlaybackState();
        video.setKeepScreenOn(resumed && player.getPlayWhenReady()
                && player.getPlaybackSuppressionReason() == Player.PLAYBACK_SUPPRESSION_REASON_NONE
                && (state == Player.STATE_BUFFERING || state == Player.STATE_READY));
    }

    @Override protected void onResume() {
        super.onResume();
        resumed = true;
        updatePlaybackScreenAwake();
        if (guide != null) loadDirectory();
        commitUpdate();
        openInstallConfirmation();
    }
    @Override protected void onPause() {
        resumed = false;
        updatePlaybackScreenAwake();
        super.onPause();
    }
    @Override protected void onStop() {
        returnToGuide();
        super.onStop();
    }

    private boolean motionEnabled() {
        return Settings.Global.getFloat(getContentResolver(), Settings.Global.ANIMATOR_DURATION_SCALE, 1f) != 0f;
    }

    private void focusHomeNavigation() {
        TextView home = navButtons.get(Page.HOME);
        if (home != null) home.requestFocusFromTouch();
    }

    @Override public void onBackPressed() {
        if (!guideVisible && activeMovie != null) ejectMovie();
        else if (!guideVisible) returnToGuide();
        else if (phonePortrait && !menuCollapsed) setMenuCollapsed(true);
        else if (activePage != Page.HOME) {
            openPage(Page.HOME);
            if (!phoneLayout) focusHomeNavigation();
        }
        else super.onBackPressed();
    }

    private void clearCassette() {
        revealMovie = null;
        View previous = cassetteMotion;
        cassetteMotion = null;
        if (cassetteAnimator != null) { cassetteAnimator.cancel(); cassetteAnimator = null; }
        if (previous != null) activityRoot.removeView(previous);
    }

    private void animateCassette(boolean inserting, VaultItem movie, Runnable complete) {
        clearCassette();
        if (!motionEnabled()) { complete.run(); return; }
        FrameLayout layer = new FrameLayout(this);
        layer.setBackground(surface(new int[]{color("#090914"), color("#3E2153"), color("#100D22")}, 0, 0, 0));
        int width = Math.min(dp(390), activityRoot.getWidth() * 3 / 4);
        int glassHeight = Math.min(dp(150), activityRoot.getHeight() / 3);
        FrameLayout cabinet = new FrameLayout(this);
        cabinet.setBackground(surface(new int[]{color("#675E70"), color("#302C3A"), color("#17151D")}, color("#87788D"), 2, 14));
        FrameLayout.LayoutParams body = new FrameLayout.LayoutParams(width, glassHeight + dp(60), Gravity.CENTER);
        layer.addView(cabinet, body);
        cabinet.setTranslationY(-glassHeight / 2f);
        TextView glass = text("", 12, true);
        glass.setGravity(Gravity.CENTER);
        glass.setBackground(surface(new int[]{color("#303B49"), color("#101A22"), color("#0A1016")}, color("#151521"), 3, 16));
        FrameLayout.LayoutParams bezel = new FrameLayout.LayoutParams(-1, glassHeight, Gravity.TOP);
        bezel.setMargins(dp(13), dp(7), dp(24), 0);
        cabinet.addView(glass, bezel);
        TextView slot = text("", 13, true);
        slot.setBackground(surface(new int[]{color("#08060C"), color("#211F33")}, color("#9F76AA"), 2, 6));
        layer.addView(slot, new FrameLayout.LayoutParams(width * 4 / 5, dp(33), Gravity.CENTER));
        CassetteView tape = new CassetteView(this, movie.title);
        tape.setPosition(inserting ? 0 : 1);
        Bitmap poster = posters.get(movie.poster);
        if (poster != null) tape.setPoster(poster);
        else if (!movie.poster.isEmpty()) posterLoader.execute(() -> {
            Bitmap loaded = moviePoster(movie);
            if (loaded != null) runOnUiThread(() -> {
                if (cassetteMotion == layer) tape.setPoster(loaded);
            });
        });
        layer.addView(tape, new FrameLayout.LayoutParams(-1, -1));
        activityRoot.addView(layer, new FrameLayout.LayoutParams(-1, -1));
        cassetteMotion = layer;
        cassetteAnimator = ValueAnimator.ofFloat(inserting ? 0 : 1, inserting ? 1 : 0);
        cassetteAnimator.setDuration(inserting ? 1800 : 850);
        cassetteAnimator.addUpdateListener(animation -> tape.setPosition((float) animation.getAnimatedValue()));
        cassetteAnimator.addListener(new AnimatorListenerAdapter() {
            @Override public void onAnimationEnd(Animator animation) {
                if (cassetteMotion != layer) return;
                cassetteAnimator = null;
                if (!inserting) { clearCassette(); complete.run(); return; }
                tape.setVisibility(View.GONE);
                glass.setText("Buffering movie…");
                revealMovie = () -> {
                    revealMovie = null;
                    glass.setText("");
                    glass.setBackground(surface(new int[]{color("#C2E6DF"), color("#467277")}, color("#171521"), 3, 16));
                    glass.setScaleY(.025f);
                    glass.animate().scaleY(1f).alpha(.7f).setDuration(420).withEndAction(() -> {
                        if (cassetteMotion != layer) return;
                        clearCassette();
                        complete.run();
                    }).start();
                };
                if (player.getPlaybackState() == Player.STATE_READY) revealMovie.run();
            }
        });
        cassetteAnimator.start();
    }

    private void ejectMovie() {
        if (ejectingTape || activeMovie == null) return;
        rememberMovie();
        ejectingTape = true;
        ++playGeneration;
        clearPlaybackDeadline();
        video.hideController();
        player.pause();
        animateCassette(false, activeMovie, () -> {
            returnToGuide();
            if (insertMovie != null) insertMovie.post(() -> {
                if (guideVisible && insertMovie != null) insertMovie.requestFocusFromTouch();
            });
        });
    }

    private void returnToGuide() {
        boolean refreshAfterStall = !guideVisible && resumed
                && (playbackFailed || playbackWaiting || liveRetries > 0 || movieRetries > 0);
        if (tunedChannel != null || activeMovie != null) {
            if (BuildConfig.TESTER_BUILD && testerConsented()) {
                try { lastPlaybackReport = snapshotForSharing(); }
                catch (JSONException ignored) { lastPlaybackReport = null; }
            } else {
                lastPublicPlayback = diagnosticSnapshot();
            }
        }
        ++playGeneration;
        recovering = false;
        finishRebuffer();
        video.removeCallbacks(watchdog);
        video.removeCallbacks(resetPlaybackRetries);
        ejectingTape = false;
        consumingRetryKey = false;
        clearCassette();
        tunedChannel = null;
        rememberMovie();
        activeMovie = null;
        playbackFailed = false;
        clearPlaybackDeadline();
        video.hideController();
        video.removeCallbacks(continueLive);
        video.removeCallbacks(upcomingProgram);
        video.removeCallbacks(liveProgramBoundary);
        player.stop();
        player.clearMediaItems();
        video.setFocusable(false);
        video.setVisibility(View.GONE);
        guideVisible = true;
        guide.setVisibility(View.VISIBLE);
        if (touchGuideButton != null) touchGuideButton.setVisibility(View.GONE);
        bannerPanel.setVisibility(View.GONE);
        bufferPanel.setVisibility(View.GONE);
        streamTitle = "";
        if (activePage == Page.LIVE) showLoadedChannels();
        if (activePage == Page.CONTINUE) syncStudioScreen();
        if (refreshAfterStall) loadDirectory();
    }
    @Override protected void onDestroy() {
        if (menuSounds != null) {
            menuSounds.release();
            menuSoundReady = false;
        }
        unregisterReceiver(updateResult);
        updateLoader.shutdownNow();
        if (updateSession >= 0 && !installerOpened) abandonUpdate(updateSession);
        if (guide != null) {
            guide.removeCallbacks(directoryRefresh);
            guide.removeCallbacks(directoryRetry);
        }
        posterLoader.shutdownNow();
        movieCatalogLoader.shutdownNow();
        movieCoverLoader.shutdownNow();
        studioLoader.shutdownNow();
        recoveryLoader.shutdownNow();
        clearPlaybackDeadline();
        video.setPlayer(null);
        player.release();
        super.onDestroy();
    }

    private static class Creator {
        final String name, description, playlist, vault, channelStatus;
        final boolean cinemaSupported;
        String id, state = "broadcasting";
        String playbackOrigin = "";
        long lastHeartbeat;
        long updatedAt;
        Creator(String name, String description, String playlist, String vault, String channelStatus, boolean cinemaSupported) {
            this.name = name;
            this.description = description;
            this.playlist = playlist;
            this.id = playlist;
            this.vault = vault;
            this.channelStatus = channelStatus;
            this.cinemaSupported = cinemaSupported;
        }
    }
    private static class Channel {
        final String name;
        String url, id = "", studioId = "", state = "broadcasting";
        String number = "", logo = "", guideUrl = "", studio = "", continuousUrl = "", fireTvUrl = "";
        String rating = "";
        int ratingRank = 10;
        long airUntil;
        JSONArray programs;
        Channel(String name, String url) { this.name = name; this.url = url; }

        String liveUrl(int retries, boolean phone) {
            if (retries >= 2) {
                if (retries == 2 && !fireTvUrl.isEmpty() && !continuousUrl.isEmpty()
                        && !fireTvUrl.equals(continuousUrl)) return continuousUrl;
                if (!url.isEmpty()) return url;
            }
            if (phone && !continuousUrl.isEmpty() && (retries == 0 || fireTvUrl.isEmpty())) return continuousUrl;
            if (!fireTvUrl.isEmpty()) return fireTvUrl;
            return continuousUrl.isEmpty() ? url : continuousUrl;
        }
    }
    private static class VaultItem {
        final String title, year, summary, poster, fullPoster;
        String url, id = "", studioId = "";
        VaultItem(String title, String year, String summary, String poster, String fullPoster, String url) {
            this.title = title;
            this.year = year;
            this.summary = summary;
            this.poster = poster;
            this.fullPoster = fullPoster;
            this.url = url;
        }
        String releaseKey() {
            String name = Normalizer.normalize(title, Normalizer.Form.NFKC).toLowerCase(Locale.ROOT)
                    .replaceAll("[^\\p{L}\\p{N}_]+", " ").trim();
            return name.isEmpty() ? url : name + "\n" + year.trim();
        }
    }
}
