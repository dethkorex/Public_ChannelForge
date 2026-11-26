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
import android.content.pm.PackageInstaller;
import android.graphics.Color;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.SystemClock;
import android.provider.Settings;
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
import androidx.media3.exoplayer.upstream.DefaultBandwidthMeter;
import androidx.media3.ui.PlayerView;
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
    private ExoPlayer player;
    private boolean renderedFrame;
    private enum Page { HOME, LIVE, CINEMA, CONTINUE, FAVORITES }
    private Page activePage = Page.HOME;
    private Page renderedAuxiliary;
    private final Map<Page, TextView> navButtons = new EnumMap<>(Page.class);
    private LinearLayout guide, creatorRail, channelRail, navigationRail, menuPanel, auxiliaryPage;
    private boolean menuCollapsed;
    private int navigationWidth;

    private TextView status, banner, updateButton, navigationUpdates;
    private LinearLayout bufferPanel, bannerPanel;
    private ProgressBar bufferSpinner;
    private TextView bufferLabel;
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
    private int lastFrameCount = -1, lastErrorCode, lastHttpCode, totalRetries;
    private String lastReason = "none", decoderName = "unknown";
    private long directoryChecked, lastCpuTime, lastCpuTick;
    private int cpuPercent = -1;
    private final ArrayDeque<String> recentEvents = new ArrayDeque<>();
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
    private final Runnable playbackDeadline = () -> recover("Studio did not deliver video before the deadline");
    private final Runnable continueLive = () -> {
        if (tunedChannel != null && !guideVisible) {
            trackLiveProgram();
            if (!hasUpcomingProgram) refreshOnAirGuide(tunedChannel);
            showBuffering("Connecting to the studio…", true);
            waitForPlayback();
            startStream(tunedChannel.continuousUrl.isEmpty() ? tunedChannel.url : tunedChannel.continuousUrl, true);
        }
    };
    private boolean directoryLoading = false;
    private boolean readInitialCache = true;
    private boolean directoryStale;
    private final Set<String> staleGuides = new HashSet<>();
    private GuideCache guideCache;
    private final ExecutorService posterLoader = Executors.newFixedThreadPool(3);
    private final ExecutorService studioLoader = Executors.newFixedThreadPool(4);
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
    private int guideOffset;
    private TextView programTitle, programTime, programElapsed, programDuration;
    private ProgressBar programProgress;
    private ImageView programArtwork;
    private Channel previewChannel;
    private final Runnable previewClock = new Runnable() {
        @Override public void run() {
            if (!resumed || !guideVisible || activePage != Page.LIVE || previewChannel == null) return;
            updateProgram(previewChannel);
            programArtwork.postDelayed(this, 1000);
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
    private static final String PREFS = "channel_forge_tv";
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
        getWindow().getDecorView().setSystemUiVisibility(5894);
        guideCache = new GuideCache(getFilesDir());
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
        bandwidth = new DefaultBandwidthMeter.Builder(this).setInitialBitrateEstimate(1_000_000L).build();
        player = new ExoPlayer.Builder(this, new DefaultRenderersFactory(this).setEnableDecoderFallback(true))
                .setMediaSourceFactory(new DefaultMediaSourceFactory(http))
                .setLoadControl(new DefaultLoadControl.Builder().setBufferDurationsMs(15000, 35000, 3500, 6000).build())
                .setBandwidthMeter(bandwidth)
                .setAudioAttributes(AudioAttributes.DEFAULT, true)
                .build();
        player.setTrackSelectionParameters(player.getTrackSelectionParameters().buildUpon()
                .setPreferredAudioLanguage("en").build());
        player.addAnalyticsListener(new androidx.media3.exoplayer.analytics.AnalyticsListener() {
            @Override public void onVideoDecoderInitialized(EventTime time, String name, long timestamp, long duration) {
                decoderName = name;
            }
        });
        video.setPlayer(player);
        video.setUseController(false);
        video.setKeepContentOnPlayerReset(true);
        video.setFocusable(false);
        video.setVisibility(View.GONE);
        root.addView(video, new FrameLayout.LayoutParams(-1, -1));
        int width = getResources().getConfiguration().screenWidthDp;
        int height = getResources().getConfiguration().screenHeightDp;
        boolean compact = width < 800 || height < 450;
        guide = new LinearLayout(this); guide.setOrientation(LinearLayout.VERTICAL);
        int inset = dp(compact ? 14 : 19);
        guide.setPadding(inset,dp(9),inset,dp(compact ? 9 : 13));
        guide.setBackground(new GradientDrawable(GradientDrawable.Orientation.TL_BR,
                new int[]{color("#050F20"), color("#09152B"), color("#20103D")}));
        LinearLayout header=new LinearLayout(this);header.setGravity(Gravity.CENTER_VERTICAL);header.setPadding(0,0,0,dp(8));
        TextView menu = focusButton("☰");
        menu.setContentDescription("Toggle navigation menu");
        menu.setPadding(dp(12), 0, dp(12), 0);
        menu.setOnClickListener(v -> toggleMenu());
        header.addView(menu);
        ImageView logo=new ImageView(this);logo.setImageResource(R.drawable.channel_forge_viewer_brand);logo.setScaleType(ImageView.ScaleType.FIT_CENTER);
        int logoSize = dp(compact ? 36 : 44);
        header.addView(logo,new LinearLayout.LayoutParams(logoSize,logoSize));
        LinearLayout brandBox=new LinearLayout(this);brandBox.setOrientation(LinearLayout.VERTICAL);
        TextView brand=text("CHANNELFORGE TV",compact ? 16 : 20,true);
        brand.setTextColor(color("#FFF229"));brand.setLetterSpacing(compact ? .08f : .16f);
        brand.setSingleLine(true);
        brand.setEllipsize(android.text.TextUtils.TruncateAt.END);
        brand.setPadding(dp(compact ? 5 : 18), dp(3), dp(5), dp(3));
        brandBox.addView(brand);
        if (!compact) {
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
        updateButton=focusButton("UPDATES  •  v"+BuildConfig.VERSION_NAME);updateButton.setOnClickListener(v->selectUpdate());LinearLayout.LayoutParams updateLayout=new LinearLayout.LayoutParams(-2,-2);updateLayout.setMargins(dp(10),0,0,0);header.addView(updateButton,updateLayout);guide.addView(header);
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
        menu.setId(View.generateViewId());
        screenSwitcher.setId(View.generateViewId());
        updateButton.setId(View.generateViewId());
        menu.setNextFocusRightId(screenSwitcher.getId());
        screenSwitcher.setNextFocusLeftId(menu.getId());
        screenSwitcher.setNextFocusRightId(updateButton.getId());
        updateButton.setNextFocusLeftId(screenSwitcher.getId());
        View line=new View(this);line.setBackgroundColor(color("#16DFF5"));guide.addView(line,new LinearLayout.LayoutParams(-1,dp(2)));
        status = text("●  CONNECTING TO THE PUBLIC CHANNEL DIRECTORY…", 12, true); status.setTextColor(color("#77F6A3"));status.setBackground(surface(new int[]{color("#111A3B"),color("#0A0B20")},color("#225E8B"),1,8));LinearLayout.LayoutParams statusLayout=new LinearLayout.LayoutParams(-1,-2);statusLayout.setMargins(0,dp(6),0,dp(6));guide.addView(status,statusLayout);
        LinearLayout columns = new LinearLayout(this); columns.setOrientation(LinearLayout.HORIZONTAL); guide.addView(columns, new LinearLayout.LayoutParams(-1, 0, 1));
        menuPanel = new LinearLayout(this);
        menuPanel.setOrientation(LinearLayout.VERTICAL);
        menuPanel.setBackground(surface(new int[]{color("#0A0B1C"), color("#150B2B")}, color("#2D365B"), 1, 10));
        navigationWidth = dp(Math.max(compact ? 116 : 138, Math.min(180, width / 6)));
        LinearLayout.LayoutParams menuLayout = new LinearLayout.LayoutParams(navigationWidth, -1);
        menuLayout.setMargins(0, 0, dp(9), 0);
        columns.addView(menuPanel, menuLayout);
        ScrollView navigationScroll = new ScrollView(this);
        navigationScroll.setFillViewport(true);
        navigationScroll.setVerticalScrollBarEnabled(false);
        LinearLayout menuContent = new LinearLayout(this);
        menuContent.setPadding(dp(6), dp(5), dp(6), dp(5));
        menuContent.setOrientation(LinearLayout.VERTICAL);
        navigationRail = new LinearLayout(this);
        navigationRail.setOrientation(LinearLayout.VERTICAL);
        menuContent.addView(navigationRail);
        creatorRail = new LinearLayout(this);
        creatorRail.setOrientation(LinearLayout.VERTICAL);
        menuContent.addView(creatorRail);
        navigationScroll.addView(menuContent);
        menuPanel.addView(navigationScroll, new LinearLayout.LayoutParams(-1, -1));
        addNavigation("⌂", "Home", Page.HOME);
        addNavigation("◉", "Live TV Guide", Page.LIVE);
        addNavigation("▣", "Rewind Cinema", Page.CINEMA);
        addNavigation("▶", "Continue Watching", Page.CONTINUE);
        addNavigation("★", "Favorites", Page.FAVORITES);
        navigationUpdates = navigationButton("↻  Updates");
        navigationUpdates.setContentDescription("Updates");
        navigationUpdates.setOnClickListener(v -> selectUpdate());
        navigationRail.addView(navigationUpdates);
        LinearLayout channelsPanel=panel();channelRail=new LinearLayout(this);channelRail.setOrientation(LinearLayout.VERTICAL);ScrollView channelScroll=new ScrollView(this);channelScroll.setVerticalScrollBarEnabled(false);channelScroll.addView(channelRail);channelsPanel.addView(channelScroll,new LinearLayout.LayoutParams(-1,0,1));columns.addView(channelsPanel,new LinearLayout.LayoutParams(0,-1,1f));
        creatorRail.addView(sectionTitle("CREATOR STUDIOS","#5DEEFF"));
        creatorRail.addView(text("Searching worldwide…",14,false));
        syncStudioScreen();
        if (height >= 420) {
            TextView help = text("↑↓  SURF CHANNELS     •     OK  WATCH / GUIDE     •     BACK  RETURN TO GUIDE", 10, true);
            help.setTextColor(color("#7184A9"));
            help.setSingleLine(true);
            guide.addView(help);
        }
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
        TextView eject = focusButton("⏏  EJECT MOVIE");
        FrameLayout.LayoutParams ejectLayout = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.RIGHT);
        ejectLayout.setMargins(0, dp(12), dp(16), 0);
        eject.setVisibility(View.GONE);
        eject.setOnClickListener(v -> ejectMovie());
        root.addView(eject, ejectLayout);
        video.setControllerVisibilityListener((PlayerView.ControllerVisibilityListener) visibility ->
                eject.setVisibility(visibility == View.VISIBLE && activeMovie != null && !ejectingTape
                        && cassetteMotion == null ? View.VISIBLE : View.GONE));
        setContentView(root);
        screenSwitcher.requestFocus();
        player.addListener(new Player.Listener() {
            @Override public void onPlaybackStateChanged(int state) {
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
                if (firstFrameMs < 0) firstFrameMs = SystemClock.elapsedRealtime() - tuneStarted;
                hidePlaybackBuffer();
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
                lastHttpCode = cause == null ? 0 : ((HttpDataSource.InvalidResponseCodeException)cause).responseCode;
                String reason = lastHttpCode == 503 || lastHttpCode == 429 ? "Studio is busy"
                        : lastHttpCode == 404 || lastHttpCode == 410 ? "Studio address or media expired"
                        : error.errorCode == PlaybackException.ERROR_CODE_BEHIND_LIVE_WINDOW ? "Live window moved"
                        : error.errorCode >= 4000 && error.errorCode < 5000 ? "Video decoder error"
                        : "Studio connection interrupted";
                recover(reason);
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
                    authoritative = primary && doc.optBoolean("registrations_complete");
                    if (primary && (authoritative || !incoming.isEmpty())) {
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
                    row.put("state", "reconnecting");
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
            if (row == null) continue;
            String playlist = row.optString("playlist_url");
            if (playlist.isEmpty() || hasCreator(result, playlist)) continue;
            Creator creator = new Creator(row.optString("name", "Creator"), row.optString("description"),
                    playlist, row.optString("vault_url"), row.optString("status_url"), row.optBoolean("cinema_supported"));
            creator.id = row.optString("id", playlist);
            creator.state = row.optString("state", row.optBoolean("online", true) ? "broadcasting" : "offline");
            creator.lastHeartbeat = row.optLong("last_heartbeat");
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
        creatorRail.removeAllViews();channelRail.removeAllViews();
        liveRail = null;
        movieRail = null;
        currentStudio = null;
        auxiliaryPage = null;
        ++movieRequest;
        status.setText(error == null ? "NO PUBLIC STUDIOS ONLINE" : "PUBLIC DIRECTORY UNAVAILABLE  •  SELECT SCAN TO RETRY");
        creatorRail.addView(sectionTitle("DISCOVERY","#5DEEFF"));
        TextView retry=focusButton("↻  SCAN PUBLIC DIRECTORY");retry.setOnClickListener(v->loadDirectory());creatorRail.addView(retry,new LinearLayout.LayoutParams(-1,-2));addGap(creatorRail,10);
        TextView pair=focusButton("＋  OPTIONAL DIRECT PAIR");pair.setOnClickListener(v->showPairingDialog());creatorRail.addView(pair,new LinearLayout.LayoutParams(-1,-2));
        creatorRail.addView(text("Pairing is optional. It connects one published studio directly while the global directory is unavailable.",12,false));
        channelRail.addView(sectionTitle("LIVE TV GUIDE","#5DEEFF"));
        TextView detail=text(error==null?"No public studios are online. Scan for channels to check again."
                :"The public directory is unreachable. Scan for channels to retry.",15,false);
        detail.setTextColor(color("#C6D1E7"));
        channelRail.addView(detail);
        retry.requestFocus();
    }

    private void showPairingDialog(){
        EditText input=new EditText(this);input.setSingleLine(true);input.setHint("https://your-studio.trycloudflare.com");input.setText(getSharedPreferences(PREFS,MODE_PRIVATE).getString(PAIRED_CREATOR,""));input.setSelectAllOnFocus(true);input.setPadding(30,18,30,18);
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("Pair ChannelForge Creator").setMessage("In Creator, open System → Publish worldwide and copy its HTTPS pairing URL.").setView(input).setNegativeButton("CANCEL",null).setNeutralButton("FORGET",(d,w)->{getSharedPreferences(PREFS,MODE_PRIVATE).edit().remove(PAIRED_CREATOR).apply();loadDirectory();}).setPositiveButton("PAIR",null).create();
        dialog.setOnShowListener(ignored->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{String address=input.getText().toString().trim();if(address.isEmpty()){input.setError("Enter the Creator HTTPS URL");return;}dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(false);status.setText("Pairing with Creator…");new Thread(()->{try{Creator creator=readPairedCreator(address);getSharedPreferences(PREFS,MODE_PRIVATE).edit().putString(PAIRED_CREATOR,address.replaceAll("/+$","")).apply();runOnUiThread(()->{dialog.dismiss();creators.clear();creators.add(creator);renderCreators();});}catch(Exception error){runOnUiThread(()->{dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(true);input.setError("Could not reach this Creator. Check that Creator is open and the URL begins with https://");});}}).start();}));
        dialog.show();
    }

    private TextView focusButton(String label) { TextView button=text(label,14,true);button.setTextColor(color("#DFFBFF"));button.setGravity(Gravity.CENTER_VERTICAL);button.setMinHeight(dp(38));button.setFocusable(true);button.setBackground(surface(new int[]{color("#151A3D"),color("#101127")},color("#1BDFF5"),1,7));button.setOnFocusChangeListener((v, focused)->{v.setBackground(surface(focused?new int[]{color("#6A145C"),color("#231454")}:new int[]{color("#151A3D"),color("#101127")},focused?color("#FF42C5"):color("#1BDFF5"),focused?2:1,7));v.setScaleX(focused?1.018f:1f);v.setScaleY(focused?1.018f:1f);v.setTranslationZ(focused?dp(5):0);});return button;}

    private void renderCreators() {
        creatorRail.removeAllViews();
        status.setText("●  " + creators.size() + " REGISTERED STUDIOS");
        creatorRail.addView(sectionTitle("CREATOR STUDIOS", "#5DEEFF"));
        Creator selected = creators.get(0);
        for (Creator creator : creators) {
            TextView button = focusButton(creator.name + "\n" + (creator.state.equals("broadcasting") ? creator.description : creator.state));
            button.setTag(creator);
            button.setOnClickListener(view -> {
                if (activePage == Page.LIVE) {
                    guideStudio = creator.playlist;
                    showLoadedChannels();
                } else loadCreator(creator);
            });
            LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(-1, -2);
            layout.setMargins(0, 0, 0, dp(9));
            creatorRail.addView(button, layout);
            if (creator.playlist.equals(selectedPlaylist)) selected = creator;
        }
        loadCreator(cinemaScreen && !selected.cinemaSupported ? firstCinemaStudio(selected) : selected);
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
        syncStudioScreen();
        for (Creator studio : creators) loadStudioGuide(studio);
        showLoadedChannels();
        if (cinemaScreen && creator.cinemaSupported && !movieShelfLoaded && !moviePageLoading)
            loadMoviePage(creator, 0);
    }

    private void loadStudioGuide(Creator creator) {
        if (!loadingGuides.add(creator.playlist)) return;
        String key = "epg:" + creator.id;
        boolean restore = !channelsByStudio.containsKey(creator.playlist);
        studioLoader.execute(() -> {
            List<Channel> loaded = null;
            String address = Uri.parse(creator.channelStatus).buildUpon().appendQueryParameter("guide", "extended").build().toString();
            if (restore) {
                String cached = guideCache.read(key);
                if (!cached.isEmpty()) try {
                    List<Channel> rows = parseOnlineChannels(cached, address);
                    runOnUiThread(() -> applyGuide(creator, rows, true));
                } catch (JSONException invalid) { android.util.Log.w("ChannelForgeGuide", "Invalid EPG snapshot"); }
            }
            boolean degraded = false;
            try {
                if (!creator.channelStatus.isEmpty()) {
                    String payload = get(address);
                    loaded = parseOnlineChannels(payload, address);
                    guideCache.write(key, payload);
                }
            } catch (Exception unavailable) {
                android.util.Log.w("ChannelForgeGuide", "Studio EPG request failed: " + unavailable.getClass().getSimpleName());
            }
            if (loaded == null) {
                degraded = true;
                try {
                    String playlist = get(creator.playlist);
                    if (playlist.startsWith("#EXTM3U")) loaded = parseM3u(playlist);
                } catch (Exception unavailable) {
                    android.util.Log.w("ChannelForgeGuide", "Playlist fallback unavailable");
                }
            }
            final List<Channel> result = loaded;
            final boolean stale = degraded;
            runOnUiThread(() -> {
                loadingGuides.remove(creator.playlist);
                applyGuide(creator, result, stale);
            });
        });
    }

    private void applyGuide(Creator creator, List<Channel> rows, boolean stale) {
        if (isFinishing() || isDestroyed() || !hasCreator(creators, creator.playlist)) return;
        if (stale) staleGuides.add(creator.playlist);
        else staleGuides.remove(creator.playlist);
        List<Channel> prior = channelsByStudio.get(creator.playlist);
        if (rows != null && (!stale || prior == null || prior.isEmpty())) {
            for (Channel channel : rows) {
                channel.studio = creator.name;
                channel.studioId = creator.id;
            }
            channelsByStudio.put(creator.playlist, rows);
        } else if (rows != null) {
            // A working playlist can restore tuning while the schedule endpoint is unavailable.
            // Only the schedule endpoint can authoritatively remove a channel and its EPG.
            for (Channel row : rows) {
                Channel existing = null;
                for (Channel previous : prior) {
                    if ((!row.id.isEmpty() && row.id.equals(previous.id)) || row.url.equals(previous.url)) {
                        existing = previous;
                        break;
                    }
                }
                if (existing == null) {
                    row.studio = creator.name;
                    row.studioId = creator.id;
                    prior.add(row);
                } else if (row.programs == null && !existing.url.equals(row.url)) {
                    existing.url = row.url;
                    existing.continuousUrl = "";
                }
            }
        }
        showLoadedChannels();
    }

    private void showLoadedChannels() {
        if (!guideVisible || liveRail == null) return;
        channels.clear();
        for (Creator studio : creators) {
            if (guideStudio.isEmpty() || studio.playlist.equals(guideStudio) || activePage == Page.FAVORITES) {
                List<Channel> loaded = channelsByStudio.get(studio.playlist);
                if (loaded != null) channels.addAll(loaded);
            }
        }
        channelIndex = Math.min(channelIndex, Math.max(0, channels.size() - 1));
        String name = currentStudio == null ? "Worldwide" : currentStudio.name;
        if (activePage == Page.LIVE) {
            name = "All studios";
            for (Creator studio : creators) {
                if (studio.playlist.equals(guideStudio)) { name = studio.name; break; }
            }
        }
        renderChannels(name);
        if (activePage == Page.FAVORITES) syncStudioScreen();
    }

    private TextView navigationButton(String label) {
        TextView button = focusButton(label);
        button.setTextSize(11);
        button.setMinHeight(dp(30));
        button.setPadding(dp(9), dp(3), dp(8), dp(3));
        button.setMaxLines(1);
        button.setEllipsize(android.text.TextUtils.TruncateAt.END);
        return button;
    }

    private void addNavigation(String icon, String label, Page page) {
        TextView button = navigationButton(icon + "  " + label);
        button.setTag(new String[]{icon, label});
        button.setContentDescription(label);
        button.setOnClickListener(v -> openPage(page));
        button.setOnFocusChangeListener((view, focused) -> paintNavigation(button, page));
        paintNavigation(button, page);
        navigationRail.addView(button);
        navButtons.put(page, button);
    }

    private void paintNavigation(TextView button, Page page) {
        boolean selected = activePage == page;
        if (selected || button.isFocused()) {
            button.setBackground(surface(new int[]{color("#251849"), color("#17102C")},
                    color(button.isFocused() ? "#FF77D9" : "#D242AA"), 1, 7));
        } else button.setBackgroundColor(Color.TRANSPARENT);
        button.setTextColor(color(selected ? "#FFFFFF" : "#B7C8E5"));
    }

    private void toggleMenu() {
        setMenuCollapsed(!menuCollapsed);
        if (activePage == Page.LIVE) showLoadedChannels();
    }

    private void setMenuCollapsed(boolean collapsed) {
        menuCollapsed = collapsed;
        menuPanel.getLayoutParams().width = menuCollapsed ? dp(44) : navigationWidth;
        menuPanel.requestLayout();
        creatorRail.setVisibility(menuCollapsed ? View.GONE : View.VISIBLE);
        for (TextView button : navButtons.values()) {
            String[] parts = (String[]) button.getTag();
            button.setText(menuCollapsed ? parts[0] : parts[0] + "  " + parts[1]);
        }
        navigationUpdates.setText(menuCollapsed ? "↻" : "↻  Updates");
    }

    private void openCinemaScreen(boolean cinema) {
        openPage(cinema ? Page.CINEMA : Page.LIVE);
    }

    private void openPage(Page page) {
        if (page == Page.LIVE && activePage != Page.LIVE) setMenuCollapsed(true);
        activePage = page;
        cinemaScreen = page == Page.CINEMA;
        if (cinemaScreen && currentStudio != null && !currentStudio.cinemaSupported) {
            Creator supported = firstCinemaStudio(currentStudio);
            if (supported != currentStudio) { loadCreator(supported); return; }
        }
        syncStudioScreen();
        if (page == Page.LIVE || page == Page.FAVORITES) showLoadedChannels();
        if (cinemaScreen && currentStudio != null && currentStudio.cinemaSupported && !movieShelfLoaded && !moviePageLoading)
            loadMoviePage(currentStudio, 0);
    }

    private Creator firstCinemaStudio(Creator fallback) {
        for (Creator item : creators) if (item.cinemaSupported) return item;
        return fallback;
    }

    private void syncStudioScreen() {
        if (channelRail == null) return;
        for (Map.Entry<Page, TextView> entry : navButtons.entrySet()) paintNavigation(entry.getValue(), entry.getKey());
        screenSwitcher.setText(cinemaScreen ? "LIVE TV GUIDE" : "REWIND CINEMA");
        if (liveRail != null) liveRail.setVisibility(activePage == Page.LIVE ? View.VISIBLE : View.GONE);
        boolean showMovies = cinemaScreen && currentStudio != null && currentStudio.cinemaSupported;
        if (movieRail != null) movieRail.setVisibility(showMovies ? View.VISIBLE : View.GONE);
        for (int i = 0; i < creatorRail.getChildCount(); i++) {
            View child = creatorRail.getChildAt(i);
            if (child.getTag() instanceof Creator) {
                Creator item = (Creator) child.getTag();
                child.setVisibility(cinemaScreen && !item.cinemaSupported ? View.GONE : View.VISIBLE);
            }
        }
        if (auxiliaryPage != null && renderedAuxiliary == activePage && activePage == Page.HOME) return;
        if (auxiliaryPage != null) channelRail.removeView(auxiliaryPage);
        auxiliaryPage = null;
        if ((activePage == Page.LIVE && liveRail != null) || (showMovies && movieRail != null)) return;
        auxiliaryPage = new LinearLayout(this);
        renderedAuxiliary = activePage;
        auxiliaryPage.setOrientation(LinearLayout.VERTICAL);
        channelRail.addView(auxiliaryPage);
        if (activePage == Page.HOME) {
            TextView hero = text("Live channels.\nOne community dial.", 29, true);
            hero.setTextColor(color("#FAFCFF"));
            hero.setPadding(dp(18), dp(14), dp(18), dp(14));
            hero.setBackground(surface(new int[]{color("#271343"), color("#081F33"), color("#2A1744")}, color("#42E9F4"), 1, 11));
            auxiliaryPage.addView(hero);
            addGap(auxiliaryPage, 8);
            addHomeActions("LIVE TV GUIDE", () -> openPage(Page.LIVE), "REWIND CINEMA", () -> openPage(Page.CINEMA));
            addGap(auxiliaryPage, 8);
            addHomeActions("FAVORITES", () -> openPage(Page.FAVORITES), "UPDATES", this::selectUpdate);
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
        } else {
            auxiliaryPage.addView(text(cinemaScreen ? "No compatible movie studios are online."
                    : "Waiting for a public studio. Select Scan for Channels to retry.", 14, false));
            TextView retry = focusButton("↻ SCAN FOR CHANNELS");
            retry.setOnClickListener(v -> loadDirectory());
            auxiliaryPage.addView(retry);
        }
    }

    private void addHomeActions(String left, Runnable leftAction, String right, Runnable rightAction) {
        LinearLayout row = new LinearLayout(this);
        TextView first = focusButton(left), second = focusButton(right);
        first.setOnClickListener(v -> leftAction.run());
        second.setOnClickListener(v -> rightAction.run());
        LinearLayout.LayoutParams leftLayout = new LinearLayout.LayoutParams(0, dp(48), 1);
        leftLayout.rightMargin = dp(8);
        row.addView(first, leftLayout);
        row.addView(second, new LinearLayout.LayoutParams(0, dp(48), 1));
        auxiliaryPage.addView(row);
    }

    private void showContinueWatching() {
        String stored = getSharedPreferences(PREFS, MODE_PRIVATE).getString("continue_movie", "");
        try {
            JSONObject entry = new JSONObject(stored);
            VaultItem movie = new VaultItem(entry.getString("title"), entry.optString("year"), "",
                    entry.optString("poster"), entry.optString("fullPoster"), entry.getString("url"));
            movie.id = entry.optString("id");
            long position = entry.optLong("position");
            if (!movie.url.isEmpty() && position > 0) {
                TextView resume = focusButton(movie.title + "  •  RESUME AT " + elapsedTime(position));
                resume.setOnClickListener(v -> playMovie(movie, position));
                auxiliaryPage.addView(resume);
                return;
            }
        } catch (JSONException ignored) { }
        auxiliaryPage.addView(text("No unfinished movies yet.", 13, false));
    }

    private void rememberMovie() {
        if (activeMovie == null) return;
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        long position = player.getCurrentPosition();
        try {
            if (movieFinished) {
                JSONObject previous = new JSONObject(prefs.getString("continue_movie", "{}"));
                if (activeMovie.url.equals(previous.optString("url"))) prefs.edit().remove("continue_movie").apply();
            } else if (position >= 8000 && !playbackWaiting) {
                JSONObject entry = new JSONObject();
                entry.put("title", activeMovie.title).put("id", activeMovie.id).put("year", activeMovie.year)
                        .put("poster", activeMovie.poster).put("fullPoster", activeMovie.fullPoster)
                        .put("url", activeMovie.url).put("position", position);
                prefs.edit().putString("continue_movie", entry.toString()).apply();
            }
        } catch (JSONException ignored) { }
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
        studioLoader.execute(() -> {
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
            if (entry.has("stream_url")) {
                Channel channel = new Channel(entry.getString("name"), entry.getString("stream_url"));
                channel.id = entry.optString("id");
                channel.state = entry.optString("state", entry.optBoolean("online", true) ? "broadcasting" : "offline");
                channel.continuousUrl = entry.optString("firetv_url", entry.optString("continuous_url"));
                channel.airUntil = entry.optLong("air_until_epoch_ms", 0);
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

    private List<Channel> parseM3u(String m3u) {
        List<Channel> result = new ArrayList<>();
        String info = "";
        for (String raw : m3u.split("\\r?\\n")) {
            String line = raw.trim();
            if (line.startsWith("#EXTINF:")) {
                info = line;
            } else if (!line.isEmpty() && !line.startsWith("#") && !info.isEmpty()) {
                int comma = info.indexOf(',');
                Channel channel = new Channel(comma >= 0 ? info.substring(comma + 1).trim() : "Channel", line);
                java.util.regex.Matcher id = M3U_ID.matcher(info), number = M3U_NUMBER.matcher(info);
                if (id.find()) channel.id = id.group(1);
                if (number.find()) channel.number = number.group(1);
                result.add(channel);
                info = "";
            }
        }
        return result;
    }

    private static final long HALF_HOUR = 30 * 60 * 1000L;

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

        LinearLayout tools = new LinearLayout(this);
        tools.setGravity(Gravity.CENTER_VERTICAL);
        guideControl(tools, "↻ SCAN FOR CHANNELS", "scan", () -> loadDirectory(), previousFocus);
        tools.addView(text(channels.size() + " channels", 11, true));
        guideControl(tools, "‹ EARLIER", "earlier", () -> moveGuide(-1), previousFocus);
        guideControl(tools, "NOW", "now", () -> { guideOffset = 0; showLoadedChannels(); }, previousFocus);
        guideControl(tools, "LATER ›", "later", () -> moveGuide(1), previousFocus);
        guideControl(tools, guideSearch.isEmpty() ? "SEARCH GUIDE" : "SEARCH: " + guideSearch,
                "search", () -> searchGuide(), previousFocus);
        addGuideStrip(tools);
        addGap(liveRail, 6);
        LinearLayout filters = new LinearLayout(this);
        guideControl(filters, "ALL STUDIOS", "studio:", () -> filterGuide(""), previousFocus);
        for (Creator studio : creators) {
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
        int viewport = getResources().getDisplayMetrics().widthPixels
                - (menuCollapsed ? dp(44) : navigationWidth) - dp(63);
        int stationWidth = dp(116);
        int ticksCount = getResources().getConfiguration().screenWidthDp < 770 ? 6 : 8;
        int tickWidth = Math.max(dp(48), (viewport - stationWidth - dp(34)) / ticksCount);
        int timelineWidth = tickWidth * ticksCount;
        long start = (System.currentTimeMillis() / HALF_HOUR + 2L * guideOffset) * HALF_HOUR;
        long end = start + ticksCount * HALF_HOUR;

        LinearLayout table = new LinearLayout(this);
        table.setOrientation(LinearLayout.VERTICAL);
        table.setBackground(surface(new int[]{color("#0D142C"), color("#090A20")}, color("#1EC6E3"), 1, 7));
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
        for (int i = 0; i < channels.size(); i++) {
            Channel channel = channels.get(i);
            if (!matchesGuide(channel)) continue;
            if (initial == null) initial = channel;
            addGuideRow(table, channel, i, start, end, stationWidth, timelineWidth, previousFocus);
        }
        if (initial != null && (previousFocus == null || getCurrentFocus() == focused)) showProgram(initial);
        if (initial == null) table.addView(text("No channels match this search.", 13, false));
        addGuideStrip(table);
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
        guideOffset = Math.max(-1, Math.min(5, guideOffset + direction));
        showLoadedChannels();
    }

    private void filterGuide(String playlist) {
        guideStudio = playlist;
        showLoadedChannels();
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
        preview.addView(screen, screenLayout);
        FrameLayout card = new FrameLayout(this);
        card.setBackground(surface(new int[]{color("#10162C"), color("#2C1350")}, color("#26D6F2"), 1, 8));
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
        detail.setPadding(dp(8), dp(4), dp(8), dp(2));
        TextView now = sectionTitle("● NOW PLAYING", "#5EF4FF");
        now.setPadding(dp(6), dp(1), dp(6), dp(1));
        detail.addView(now);
        programTitle = text("Select a channel from the guide", 17, true);
        programTitle.setTextColor(color("#FFF16B"));
        programTitle.setSingleLine(true);
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
        preview.addView(card, new LinearLayout.LayoutParams(0, -1, 1));
        int previewHeight = getResources().getConfiguration().screenHeightDp < 420 ? 87 : 106;
        liveRail.addView(preview, new LinearLayout.LayoutParams(-1, dp(previewHeight)));
        addGap(liveRail, 8);
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

    private void updateProgram(Channel channel) {
        long now = System.currentTimeMillis();
        JSONObject item = null;
        if (channel.programs != null) {
            for (int i = 0; i < channel.programs.length(); i++) {
                JSONObject entry = channel.programs.optJSONObject(i);
                if (entry != null && guideTimeMillis(entry, "start") <= now && guideTimeMillis(entry, "stop") > now) {
                    item = entry;
                    break;
                }
            }
        }
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
        view.setTag(artworkKey);
        view.setImageDrawable(null);
        if (!artworkKey.trim().isEmpty()) posterLoader.execute(() -> {
            Set<String> tried = new HashSet<>();
            for (String url : artwork) {
                if (url.isEmpty() || !tried.add(url)) continue;
                Bitmap image = posterBitmap(url);
                if (image == null) continue;
                runOnUiThread(() -> {
                    if (view.isAttachedToWindow() && artworkKey.equals(view.getTag())) view.setImageBitmap(image);
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
        TextView station = text("CH " + displayNumber(channel, index) + "  " + channel.name + "\n" + channel.studio
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
        tune.setOnClickListener(view -> play(index));
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
        LinearLayout strip = new LinearLayout(this);
        for (String value : new String[]{"all","60s","70s","80s","90s","2000s","recent"}) {
            String label = value.equals("recent") ? "2010+" : value.toUpperCase(Locale.ROOT);
            TextView button = navigationButton(label);
            button.setSelected(value.equals(movieDecade));
            if (button.isSelected()) button.setTextColor(color("#FFF088"));
            button.setContentDescription(label + (button.isSelected() ? ", selected" : ""));
            button.setOnClickListener(v -> { movieDecade = value; reloadMovieFilters(creator); });
            strip.addView(button);
        }
        TextView search = navigationButton(movieSearch.isEmpty() ? "Search movies" : movieSearch);
        search.setOnClickListener(v -> {
            EditText input = new EditText(this);
            input.setSingleLine(true);
            input.setText(movieSearch);
            new AlertDialog.Builder(this).setTitle("Search movies").setView(input)
                    .setPositiveButton("Search", (d,w) -> { movieSearch = input.getText().toString().trim(); reloadMovieFilters(creator); })
                    .setNeutralButton("Clear", (d,w) -> { movieSearch = ""; reloadMovieFilters(creator); })
                    .setNegativeButton("Cancel", null).show();
        });
        strip.addView(search);
        addMovieChoices(strip, creator, true);
        addMovieChoices(strip, creator, false);
        addHorizontalStrip(movieRail, strip);
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
        row.addView(button);
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
        movieRail.addView(hero, new LinearLayout.LayoutParams(-1, dp(62)));
        addGap(movieRail, 6);
        LinearLayout studios = new LinearLayout(this);
        for (Creator option : creators) {
            if (!option.cinemaSupported) continue;
            TextView studio = navigationButton(option.name);
            if (option.playlist.equals(creator.playlist)) studio.setTextColor(color("#FFF088"));
            studio.setOnClickListener(view -> loadCreator(option));
            LinearLayout.LayoutParams button = new LinearLayout.LayoutParams(-2, -2);
            button.setMargins(0, 0, dp(5), 0);
            studios.addView(studio, button);
        }
        if (studios.getChildCount() > 1) addHorizontalStrip(movieRail, studios);
        addMovieFilters(creator);
        if (vaultItems.isEmpty()) {
            movieRail.addView(text("No movies match this selection.", 14, false));
            return;
        }
        LinearLayout content = new LinearLayout(this);
        content.setBaselineAligned(false);
        content.setPadding(0, dp(8), 0, 0);
        int screenHeight = getResources().getConfiguration().screenHeightDp;
        int height = Math.max(106, Math.min(430, screenHeight - (screenHeight < 450 ? 270 : 318)));
        LinearLayout shelf = new LinearLayout(this);
        shelf.setOrientation(LinearLayout.VERTICAL);
        movieShelf = shelf;
        shelf.setPadding(dp(4), dp(4), dp(6), dp(8));
        ScrollView results = new ScrollView(this);
        results.setFillViewport(true);
        results.setNestedScrollingEnabled(true);
        results.addView(shelf);
        content.addView(results, new LinearLayout.LayoutParams(0, dp(height), 1.15f));
        movieDetails = panel();
        ScrollView detailsScroll = new ScrollView(this);
        detailsScroll.setFillViewport(true);
        detailsScroll.setNestedScrollingEnabled(true);
        detailsScroll.addView(movieDetails);
        content.addView(detailsScroll, new LinearLayout.LayoutParams(0, dp(height), 1f));
        movieRail.addView(content);
        growMovieShelf(creator);
        showMovieDetails(vaultItems.get(0));
    }

    private void showMovieDetails(VaultItem item) {
        movieDetails.removeAllViews();
        LinearLayout top = new LinearLayout(this);
        top.setGravity(Gravity.CENTER_VERTICAL);
        FrameLayout caseFrame = new FrameLayout(this);
        caseFrame.setPadding(dp(9), dp(4), dp(4), dp(6));
        caseFrame.setBackground(surface(new int[]{color("#98745F"), color("#23172E"), color("#130E21")},
                color("#B38D73"), 1, 5));
        ImageView cover = new ImageView(this);
        cover.setScaleType(ImageView.ScaleType.FIT_CENTER);
        cover.setBackgroundColor(color("#0C0B1B"));
        caseFrame.addView(cover, new FrameLayout.LayoutParams(-1, -1));
        top.addView(caseFrame, new LinearLayout.LayoutParams(dp(70), dp(101)));
        if (!item.poster.isEmpty()) posterLoader.execute(() -> updatePoster(moviePoster(item), cover));
        LinearLayout labels = new LinearLayout(this);
        labels.setOrientation(LinearLayout.VERTICAL);
        labels.setPadding(dp(9), 0, 0, 0);
        TextView label = sectionTitle("SELECTED CASSETTE", "#63EFFF");
        label.setPadding(dp(4), 0, 0, dp(3));
        labels.addView(label);
        TextView title = text(item.title + (item.year.isEmpty() ? "" : "  ·  " + item.year), 16, true);
        title.setTextColor(color("#FFE275"));
        title.setPadding(dp(4), 0, 0, dp(5));
        labels.addView(title);
        top.addView(labels, new LinearLayout.LayoutParams(0, -2, 1));
        movieDetails.addView(top);
        addGap(movieDetails, 8);
        insertMovie = focusButton("▶  INSERT MOVIE");
        insertMovie.setOnClickListener(v -> playMovie(item));
        movieDetails.addView(insertMovie);
        if (!item.summary.isEmpty()) {
            TextView summary = text(item.summary, 12, false);
            summary.setPadding(dp(4), dp(5), dp(4), dp(4));
            movieDetails.addView(summary);
        }
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
        LinearLayout box = new LinearLayout(this);
        box.setGravity(Gravity.CENTER_VERTICAL);
        box.setPadding(dp(10), dp(5), dp(7), dp(5));
        box.setFocusable(true);
        box.setContentDescription(item.title + (item.year.isEmpty() ? "" : ", " + item.year));
        int[] sleeve = {color("#5D493B"), color("#151021"), color("#452044")};
        box.setBackground(surface(sleeve, color("#C9AE83"), 1, 6));
        ImageView poster = new ImageView(this);
        poster.setScaleType(ImageView.ScaleType.CENTER_CROP);
        poster.setBackgroundColor(color("#201528"));
        FrameLayout frame = new FrameLayout(this);
        frame.setPadding(dp(1), dp(1), dp(1), dp(1));
        frame.setBackgroundColor(color("#E84998"));
        frame.addView(poster, new FrameLayout.LayoutParams(-1, -1));
        box.addView(frame, new LinearLayout.LayoutParams(dp(47), dp(30)));
        TextView label = text(item.title, 13, true);
        label.setMaxLines(2);
        label.setTextColor(color("#F3D8AE"));
        label.setPadding(dp(8), 0, dp(4), 0);
        box.addView(label, new LinearLayout.LayoutParams(0, -2, 1));
        TextView format = text("VHS", 10, true);
        format.setPadding(dp(3), dp(3), dp(3), dp(3));
        format.setTextColor(color("#FFAD82"));
        box.addView(format);
        LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(-1, dp(42));
        layout.setMargins(0, 0, 0, dp(5));
        box.setLayoutParams(layout);
        box.setOnFocusChangeListener((view, focused) -> {
            boolean motion = motionEnabled();
            view.animate().translationY(focused && motion ? -dp(2) : 0)
                    .setDuration(motion ? 160 : 0).start();
            view.setBackground(surface(sleeve, color(focused ? "#FF68D7" : "#C9AE83"), focused ? 3 : 1, 6));
            if (focused) showMovieDetails(item);
        });
        box.setOnClickListener(view -> {
            showMovieDetails(item);
            insertMovie.requestFocusFromTouch();
        });
        if (!item.poster.isEmpty()) posterLoader.execute(() -> updatePoster(moviePoster(item), poster));
        return box;
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
        if (updateBusy) return;
        if (latestUpdateNotes.isEmpty()) {
            checkUpdates(true);
        } else showUpdateNotes();
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
                    if (requested) showUpdateNotes();
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

    private void startStream(String url, boolean live) {
        renderedFrame = false;
        recentLiveStalls.clear();
        lastFrameChange = SystemClock.elapsedRealtime();
        lastFrameCount = -1;
        video.removeCallbacks(watchdog);
        video.postDelayed(watchdog, 2000);
        video.removeCallbacks(resetPlaybackRetries);
        MediaItem.Builder media = new MediaItem.Builder().setUri(url).setMimeType(MimeTypes.APPLICATION_M3U8);
        if (live) {
            media.setLiveConfiguration(new MediaItem.LiveConfiguration.Builder().setTargetOffsetMs(12000).build());
            player.setMediaItem(media.build());
        } else {
            player.setMediaItem(media.build(), resumePosition);
        }
        player.setPlayWhenReady(live);
        player.prepare();
    }

    private void beginTune() {
        ++playGeneration;
        recovering = false;
        finishRebuffer();
        totalRebufferMs = rebufferCount = 0;
        setBitrateCap(Integer.MAX_VALUE);
        totalRetries = liveRetries = movieRetries = lastErrorCode = lastHttpCode = 0;
        resumePosition = 0;
        lastReason = "none";
        tuneStarted = SystemClock.elapsedRealtime();
        firstFrameMs = -1;
    }

    private void recover(String reason) {
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
        if (activeMovie != null && (renderedFrame || player.getCurrentPosition() > 0))
            resumePosition = Math.max(0, player.getCurrentPosition());
        int attempt = tunedChannel != null ? ++liveRetries : ++movieRetries;
        int generation = ++playGeneration;
        player.stop();
        if (attempt > 3) {
            recovering = false;
            playbackFailed = true;
            if (activeMovie != null) clearCassette();
            showBuffering(reason + ". Press OK to retry or Back for the guide.", false);
            return;
        }
        totalRetries++;
        showBuffering(reason + ". Reconnecting (" + attempt + "/3)…", true);
        final Channel channel = tunedChannel;
        final VaultItem film = activeMovie;
        final Creator source = channel == null ? currentStudio : studioFor(channel);
        boolean refreshRoute = attempt > 1 || lastHttpCode == 404 || lastHttpCode == 410;
        video.postDelayed(() -> {
            if (generation != playGeneration || guideVisible || isDestroyed()) return;
            if (!refreshRoute || source == null) {
                resumeAfterRecovery(channel, film);
                return;
            }
            // Retry the known route if catalog I/O or queued studio work takes too long.
            Runnable routeDeadline = () -> {
                if (generation == playGeneration && !guideVisible && !isDestroyed()) {
                    ++playGeneration;
                    resumeAfterRecovery(channel, film);
                }
            };
            video.postDelayed(routeDeadline, 8000);
            studioLoader.execute(() -> {
                String updatedUrl = "";
                Channel fresh = null;
                try {
                    Creator origin = source;
                    try {
                        for (Creator entry : parseCreators(new JSONObject(get(BuildConfig.DIRECTORY_URL)))) {
                            if (source.id.equals(entry.id)) { origin = entry; break; }
                        }
                    } catch (Exception unavailable) {
                        android.util.Log.w("ChannelForgePlayback", "Directory unavailable during route refresh");
                    }
                    if (channel != null && origin != null && !origin.channelStatus.isEmpty()) {
                        String address = Uri.parse(origin.channelStatus).buildUpon().appendQueryParameter("guide", "extended").build().toString();
                        for (Channel row : parseOnlineChannels(get(address), address)) {
                            if ((!channel.id.isEmpty() && channel.id.equals(row.id)) || channel.url.equals(row.url)) {
                                fresh = row;
                                break;
                            }
                        }
                    } else if (film != null && origin != null && !origin.vault.isEmpty()) {
                        String address = Uri.parse(origin.vault).buildUpon().appendQueryParameter("q", film.title)
                                .appendQueryParameter("limit", "50").build().toString();
                        for (VaultItem row : parseVault(new JSONObject(get(address)))) {
                            if ((!film.id.isEmpty() && film.id.equals(row.id)) || row.releaseKey().equals(film.releaseKey())) {
                                updatedUrl = row.url; break;
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
                    if (channel != null) {
                        if (replacement != null) {
                            channel.url = replacement.url;
                            channel.continuousUrl = replacement.continuousUrl;
                            channel.programs = replacement.programs;
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
        }, 750L << (attempt - 1));
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
        for (Creator entry : creators) {
            if (channel.studioId.equals(entry.id)) return entry;
        }
        return null;
    }

    private void play(int index) {
        if (channels.isEmpty()) return;
        channelIndex = (index + channels.size()) % channels.size();
        beginTune();
        tunedChannel = channels.get(channelIndex);
        tunedProgramEnd = 0;
        upcomingDue = upcomingShown = false;
        transitionAt = 0;
        streamTitle = tunedChannel.name;
        liveRetries = 0;
        clearPlaybackDeadline();
        activeMovie = null;
        guide.setVisibility(View.GONE);
        guideVisible = false;
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
        animateCassette(true, movie, () -> {
            if (!ejectingTape && activeMovie == movie) player.play();
        });
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

    @Override public boolean dispatchKeyEvent(KeyEvent event) {
        int key = event.getKeyCode();
        boolean select = key == KeyEvent.KEYCODE_DPAD_CENTER || key == KeyEvent.KEYCODE_ENTER;
        if (!guideVisible && select && (playbackFailed || consumingRetryKey)) {
            if (event.getAction() == KeyEvent.ACTION_DOWN && event.getRepeatCount() == 0 && playbackFailed) {
                consumingRetryKey = true;
                if (tunedChannel != null) play(channelIndex);
                else if (activeMovie != null) {
                    movieRetries = 0;
                    playbackFailed = false;
                    recover("Retrying movie");
                }
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

    private JSONObject diagnosticSnapshot() {
        JSONObject result = new JSONObject();
        try {
            result.put("app", BuildConfig.VERSION_NAME).put("engine", "Media3 ExoPlayer")
                    .put("sdk", Build.VERSION.SDK_INT).put("model", Build.MODEL)
                    .put("directoryLastChecked", directoryChecked).put("directoryCached", directoryStale)
                    .put("studioCount", creators.size()).put("staleGuides", staleGuides.size())
                    .put("firstFrameMs", firstFrameMs).put("bufferMs", player.getTotalBufferedDuration())
                    .put("bandwidthEstimateBps", bandwidth.getBitrateEstimate()).put("retries", totalRetries)
                    .put("temporaryBitrateCapBps", temporaryBitrateCap == Integer.MAX_VALUE ? -1 : temporaryBitrateCap)
                    .put("postStartRebuffers", rebufferCount)
                    .put("postStartRebufferMs", totalRebufferMs + (rebufferStarted == 0 ? 0 : SystemClock.elapsedRealtime() - rebufferStarted))
                    .put("error", lastReason).put("playerErrorCode", lastErrorCode).put("httpError", lastHttpCode)
                    .put("decoder", decoderName).put("processCpuPercentOfOneCore", cpuPercent)
                    .put("pssKiB", android.os.Debug.getPss()).put("events", new JSONArray(recentEvents));
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
        JSONObject snapshot = diagnosticSnapshot();
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
            if (event.getRepeatCount() == 0) showDiagnostics();
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

    private boolean motionEnabled() {
        return Settings.Global.getFloat(getContentResolver(), Settings.Global.ANIMATOR_DURATION_SCALE, 1f) != 0f;
    }

    @Override public void onBackPressed() {
        if (!guideVisible && activeMovie != null) ejectMovie();
        else if (!guideVisible) returnToGuide();
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
        bannerPanel.setVisibility(View.GONE);
        bufferPanel.setVisibility(View.GONE);
        streamTitle = "";
        if (activePage == Page.LIVE) showLoadedChannels();
        if (activePage == Page.CONTINUE) syncStudioScreen();
        if (refreshAfterStall) loadDirectory();
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
        final boolean cinemaSupported;
        String id, state = "broadcasting";
        long lastHeartbeat;
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
        String number = "", logo = "", guideUrl = "", studio = "", continuousUrl = "";
        long airUntil;
        JSONArray programs;
        Channel(String name, String url) { this.name = name; this.url = url; }
    }
    private static class VaultItem {
        final String title, year, summary, poster, fullPoster;
        String url, id = "";
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
