package tv.channelforge.viewer;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Color;
import android.graphics.BitmapFactory;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.widget.*;
import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private final List<Creator> creators = new ArrayList<>();
    private final List<Channel> channels = new ArrayList<>();
    private final List<VaultItem> vaultItems = new ArrayList<>();
    private VideoView video;
    private LinearLayout guide, creatorRail, channelRail;
    private TextView status, banner, updateButton;
    private int channelIndex = 0;
    private boolean guideVisible = true;
    private Channel tunedChannel;
    private int liveRetries;
    private final Runnable continueLive = () -> {
        if (tunedChannel != null && !guideVisible) {
            video.setVideoURI(Uri.parse(tunedChannel.url));
            video.start();
        }
    };
    private boolean directoryLoading = false;
    private final ExecutorService posterLoader = Executors.newFixedThreadPool(3);
    private int studioRequest = 0;
    private String selectedPlaylist = "";
    private static final String PREFS = "channel_forge_tv";
    private static final String PAIRED_CREATOR = "paired_creator_url";
    private static final long DIRECTORY_REFRESH_MS = 60_000L;
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
        loadDirectory();
        guide.postDelayed(directoryRefresh, DIRECTORY_REFRESH_MS);
        checkUpdates(updateButton);
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
        video = new VideoView(this); root.addView(video, new FrameLayout.LayoutParams(-1, -1));
        guide = new LinearLayout(this); guide.setOrientation(LinearLayout.VERTICAL); guide.setPadding(dp(26),dp(16),dp(26),dp(15)); guide.setBackgroundColor(Color.argb(238, 4, 5, 20));
        LinearLayout header=new LinearLayout(this);header.setGravity(Gravity.CENTER_VERTICAL);header.setPadding(0,0,0,dp(10));
        ImageView logo=new ImageView(this);logo.setImageResource(R.mipmap.channel_forge_tv_icon);logo.setScaleType(ImageView.ScaleType.FIT_CENTER);header.addView(logo,new LinearLayout.LayoutParams(dp(68),dp(68)));
        LinearLayout brandBox=new LinearLayout(this);brandBox.setOrientation(LinearLayout.VERTICAL);TextView brand=text("CHANNELFORGE TV",25,true);brand.setTextColor(color("#FFF229"));brand.setLetterSpacing(.16f);brandBox.addView(brand);TextView subtitle=text("THE COMMUNITY DIAL",11,true);subtitle.setTextColor(color("#55EFFF"));subtitle.setLetterSpacing(.28f);brandBox.addView(subtitle);header.addView(brandBox,new LinearLayout.LayoutParams(0,-2,1));
        TextView pair=focusButton("OPTIONAL DIRECT PAIR");pair.setOnClickListener(v->showPairingDialog());header.addView(pair);
        updateButton=focusButton("UPDATES  •  v"+BuildConfig.VERSION_NAME);updateButton.setOnClickListener(v->checkUpdates(updateButton));LinearLayout.LayoutParams updateLayout=new LinearLayout.LayoutParams(-2,-2);updateLayout.setMargins(dp(10),0,0,0);header.addView(updateButton,updateLayout);guide.addView(header);
        View line=new View(this);line.setBackgroundColor(color("#16DFF5"));guide.addView(line,new LinearLayout.LayoutParams(-1,dp(2)));
        status = text("●  CONNECTING TO THE PUBLIC CHANNEL DIRECTORY…", 12, true); status.setTextColor(color("#77F6A3"));status.setBackground(surface(new int[]{color("#111A3B"),color("#0A0B20")},color("#225E8B"),1,11));LinearLayout.LayoutParams statusLayout=new LinearLayout.LayoutParams(-1,-2);statusLayout.setMargins(0,dp(12),0,dp(12));guide.addView(status,statusLayout);
        LinearLayout columns = new LinearLayout(this); columns.setOrientation(LinearLayout.HORIZONTAL); guide.addView(columns, new LinearLayout.LayoutParams(-1, 0, 1));
        LinearLayout creatorsPanel=panel();creatorRail=new LinearLayout(this);creatorRail.setOrientation(LinearLayout.VERTICAL);ScrollView creatorScroll=new ScrollView(this);creatorScroll.setVerticalScrollBarEnabled(false);creatorScroll.addView(creatorRail);creatorsPanel.addView(creatorScroll,new LinearLayout.LayoutParams(-1,0,1));LinearLayout.LayoutParams creatorsLayout=new LinearLayout.LayoutParams(0,-1,.29f);creatorsLayout.setMargins(0,0,dp(12),0);columns.addView(creatorsPanel,creatorsLayout);
        LinearLayout channelsPanel=panel();channelRail=new LinearLayout(this);channelRail.setOrientation(LinearLayout.VERTICAL);ScrollView channelScroll=new ScrollView(this);channelScroll.setVerticalScrollBarEnabled(false);channelScroll.addView(channelRail);channelsPanel.addView(channelScroll,new LinearLayout.LayoutParams(-1,0,1));columns.addView(channelsPanel,new LinearLayout.LayoutParams(0,-1,.71f));
        creatorRail.addView(sectionTitle("COMMUNITY STUDIOS","#5DEEFF"));creatorRail.addView(text("Searching worldwide…",14,false));
        channelRail.addView(sectionTitle("WELCOME TO CHANNELFORGE TV","#FF48C8"));TextView welcome=text("Live creator channels and Rewind Cinema in one standalone Fire TV app.",24,true);welcome.setTextColor(color("#FFF229"));channelRail.addView(welcome);channelRail.addView(text("No Plex setup is needed on this Fire TV. Public studios appear here automatically when they are online.",15,false));
        TextView help = text("↑↓  SURF CHANNELS     •     OK  WATCH / GUIDE     •     BACK  RETURN TO GUIDE", 10, true); help.setTextColor(color("#7184A9")); guide.addView(help);
        root.addView(guide, new FrameLayout.LayoutParams(-1,-1));
        banner = text("", 18, true); banner.setBackground(surface(new int[]{color("#111938"),color("#290A3E")},color("#12DFF9"),2,12)); banner.setVisibility(View.GONE); FrameLayout.LayoutParams bp=new FrameLayout.LayoutParams(-1,-2,Gravity.BOTTOM); bp.setMargins(dp(35),0,dp(35),dp(35)); root.addView(banner,bp);
        setContentView(root);
        video.setOnPreparedListener(player -> {
            liveRetries = 0;
            status.setText("●  STREAM READY  •  ENGLISH AUDIO PREFERRED");
            player.start();
        });
        video.setOnCompletionListener(player -> {
            if (tunedChannel != null && !guideVisible) scheduleLive(400);
            else if (!guideVisible) returnToGuide();
        });
        video.setOnErrorListener((player, what, extra) -> {
            if (tunedChannel != null && !guideVisible && liveRetries < 3) {
                showBanner("Reconnecting to " + tunedChannel.name + "…");
                scheduleLive(1200L * ++liveRetries);
            } else {
                showBanner("Stream unavailable — check the creator server");
            }
            return true;
        });
    }

    private String get(String address) throws Exception {
        HttpURLConnection connection=(HttpURLConnection)new URL(address).openConnection(); connection.setConnectTimeout(10000); connection.setReadTimeout(15000); connection.setRequestProperty("Accept","application/json, application/x-mpegURL, */*"); connection.setRequestProperty("User-Agent","ChannelForgeTV/"+BuildConfig.VERSION_NAME);
        int code=connection.getResponseCode(); if(code<200||code>=300)throw new IOException("HTTP "+code+" from "+address);
        try(InputStream input=connection.getInputStream()){ return new String(readAll(input), StandardCharsets.UTF_8); }
    }

    private byte[] readAll(InputStream input) throws IOException { ByteArrayOutputStream out=new ByteArrayOutputStream(); byte[] buffer=new byte[8192]; int count; while((count=input.read(buffer))>=0) out.write(buffer,0,count); return out.toByteArray(); }

    private void loadDirectory() {
        if(directoryLoading)return;
        directoryLoading=true;
        status.setText("Scanning the public ChannelForge directory…");
        new Thread(() -> {
            List<Creator> discovered=new ArrayList<>();
            String paired=getSharedPreferences(PREFS,MODE_PRIVATE).getString(PAIRED_CREATOR,"");
            if(!paired.isEmpty())try{discovered.add(readPairedCreator(paired));}catch(Exception ignored){}
            Exception last=null;
            for(String address:new String[]{BuildConfig.DIRECTORY_URL,BuildConfig.DIRECTORY_FALLBACK_URL}){
                try{
                    JSONArray values=new JSONObject(get(address)).getJSONArray("creators");
                    for(int i=0;i<values.length();i++){
                        JSONObject o=values.getJSONObject(i); String playlist=o.getString("playlist_url");
                        if(!hasCreator(discovered,playlist))discovered.add(new Creator(o.getString("name"),o.optString("description"),playlist,o.optString("vault_url"),o.optString("status_url")));
                    }
                    if(!discovered.isEmpty())break;
                }catch(Exception error){last=error;}
            }
            Exception failure=last;
            runOnUiThread(()->{directoryLoading=false;creators.clear();creators.addAll(discovered);if(!creators.isEmpty())renderCreators();else renderDirectoryRecovery(failure);});
        }).start();
    }

    private boolean hasCreator(List<Creator> values,String playlist){for(Creator creator:values)if(creator.playlist.equals(playlist))return true;return false;}

    private Creator readPairedCreator(String raw) throws Exception {
        String base=raw.trim().replaceAll("/+$","");
        if(base.endsWith("/api/public/channels"))base=base.substring(0,base.length()-"/api/public/channels".length());
        if(!base.startsWith("https://"))throw new IOException("Pairing URLs must use HTTPS");
        JSONObject state=new JSONObject(get(base+"/api/public/channels"));
        if(!state.has("channels"))throw new IOException("That URL is not a ChannelForge Creator");
        String host=Uri.parse(base).getHost();
        return new Creator(host==null?"Paired Creator":host,"Paired directly with this Fire TV",base+"/exports/channels.m3u",base+"/api/public/vault",base+"/api/public/channels");
    }

    private void renderDirectoryRecovery(Exception error){
        creatorRail.removeAllViews();channelRail.removeAllViews();
        status.setText("●  STANDALONE TV READY  •  NO PUBLIC STUDIOS ONLINE");
        creatorRail.addView(sectionTitle("DISCOVERY","#5DEEFF"));
        TextView retry=focusButton("↻  SCAN PUBLIC DIRECTORY");retry.setOnClickListener(v->loadDirectory());creatorRail.addView(retry,new LinearLayout.LayoutParams(-1,-2));addGap(creatorRail,10);
        TextView pair=focusButton("＋  OPTIONAL DIRECT PAIR");pair.setOnClickListener(v->showPairingDialog());creatorRail.addView(pair,new LinearLayout.LayoutParams(-1,-2));
        creatorRail.addView(text("Pairing is optional. It connects one published studio directly while the global directory is unavailable.",12,false));
        channelRail.addView(sectionTitle("CHANNELFORGE TV","#FF48C8"));
        TextView hero=text("TURN THE DIAL.\nFIND SOMETHING UNFORGETTABLE.",27,true);hero.setTextColor(color("#FFF229"));hero.setBackground(surface(new int[]{color("#1A1450"),color("#32103F")},color("#12DFF9"),1,14));channelRail.addView(hero,new LinearLayout.LayoutParams(-1,-2));addGap(channelRail,12);
        TextView ready=text("✓  THE VIEWER IS INSTALLED AND WORKING",16,true);ready.setTextColor(color("#70FF9A"));channelRail.addView(ready);
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
        selectedPlaylist = creator.playlist;
        status.setText("Loading " + creator.name + "…");
        int request = ++studioRequest;
        new Thread(() -> {
            List<Channel> loaded = Collections.emptyList();
            List<VaultItem> movies = Collections.emptyList();
            try {
                loaded = creator.channelStatus.isEmpty()
                        ? parseM3u(get(creator.playlist))
                        : parseOnlineChannels(get(creator.channelStatus));
            } catch (Exception ignored) {}
            if (!creator.vault.isEmpty()) {
                try { movies = parseVault(get(creator.vault)); } catch (Exception ignored) {}
            }
            List<Channel> availableChannels = loaded;
            List<VaultItem> availableMovies = movies;
            runOnUiThread(() -> {
                if (isFinishing() || isDestroyed() || request != studioRequest) return;
                channels.clear();
                channels.addAll(availableChannels);
                vaultItems.clear();
                vaultItems.addAll(availableMovies);
                channelIndex = 0;
                renderChannels(creator.name);
            });
        }).start();
    }

    private List<Channel> parseOnlineChannels(String payload) throws JSONException {
        List<Channel> result = new ArrayList<>();
        JSONArray entries = new JSONObject(payload).getJSONArray("channels");
        for (int i = 0; i < entries.length(); i++) {
            JSONObject entry = entries.getJSONObject(i);
            if (entry.optBoolean("online")) {
                result.add(new Channel(entry.getString("name"), entry.getString("stream_url")));
            }
        }
        return result;
    }

    private List<VaultItem> parseVault(String value)throws Exception{List<VaultItem> result=new ArrayList<>();JSONArray items=new JSONObject(value).getJSONArray("items");for(int i=0;i<items.length();i++){JSONObject item=items.getJSONObject(i);result.add(new VaultItem(item.optString("title","Untitled"),item.optString("year"),item.optString("summary"),item.optString("poster_url"),item.getString("stream_url")));}return result;}

    private List<Channel> parseM3u(String m3u){List<Channel> result=new ArrayList<>();String pending="";for(String raw:m3u.split("\\r?\\n")){String line=raw.trim();if(line.startsWith("#EXTINF:")){int comma=line.lastIndexOf(',');pending=comma>=0?line.substring(comma+1).trim():"Channel";}else if(!line.isEmpty()&&!line.startsWith("#")&&!pending.isEmpty()){result.add(new Channel(pending,line));pending="";}}return result;}

    private void renderChannels(String creator) {
        channelRail.removeAllViews();
        status.setText("●  " + creator.toUpperCase(Locale.US) + "  •  " + channels.size()
                + " LIVE CHANNELS  •  " + vaultItems.size() + " MOVIES");
        channelRail.addView(sectionTitle("LIVE CHANNELS", "#5DEEFF"));
        for (int i = 0; i < channels.size(); i++) {
            final int index = i;
            Channel channel = channels.get(i);
            TextView button = focusButton(String.format(Locale.US, "CH %03d   %s\n● LIVE NOW  •  PRESS OK TO WATCH", i + 1, channel.name));
            button.setOnClickListener(v -> play(index));
            LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(-1, -2);
            layout.setMargins(0, 0, 0, dp(8));
            channelRail.addView(button, layout);
        }
        channelRail.addView(sectionTitle("REWIND CINEMA", "#FF48C8"));
        if (vaultItems.isEmpty()) {
            channelRail.addView(text("No movies available from this studio.", 14, false));
            return;
        }
        List<VaultItem> movies = new ArrayList<>(vaultItems);
        LinearLayout shelf = new LinearLayout(this);
        shelf.setPadding(dp(8), dp(10), dp(8), dp(20));
        appendMovies(shelf, movies);
        HorizontalScrollView scroll = new HorizontalScrollView(this) {
            @Override protected void onScrollChanged(int left, int top, int oldLeft, int oldTop) {
                super.onScrollChanged(left, top, oldLeft, oldTop);
                if (left + getWidth() + dp(640) >= shelf.getWidth()) appendMovies(shelf, movies);
            }
        };
        scroll.setHorizontalScrollBarEnabled(false);
        scroll.addView(shelf);
        channelRail.addView(scroll, new LinearLayout.LayoutParams(-1, -2));
    }

    private void appendMovies(LinearLayout shelf, List<VaultItem> movies) {
        int start = shelf.getChildCount();
        int end = Math.min(start + 18, movies.size());
        for (int i = start; i < end; i++) shelf.addView(vaultBox(movies.get(i)));
    }

    private View vaultBox(VaultItem item){LinearLayout box=new LinearLayout(this);box.setOrientation(LinearLayout.VERTICAL);box.setPadding(8,8,8,8);box.setFocusable(true);GradientDrawable normal=new GradientDrawable();normal.setColor(Color.rgb(14,12,35));normal.setStroke(2,Color.rgb(0,220,245));normal.setCornerRadius(8);box.setBackground(normal);ImageView poster=new ImageView(this);poster.setScaleType(ImageView.ScaleType.CENTER_CROP);poster.setBackgroundColor(Color.rgb(30,15,55));box.addView(poster,new LinearLayout.LayoutParams(185,255));TextView title=text(item.title+(item.year.isEmpty()?"":" ("+item.year+")"),13,true);title.setMaxLines(2);box.addView(title,new LinearLayout.LayoutParams(185,70));LinearLayout.LayoutParams layout=new LinearLayout.LayoutParams(205,350);layout.setMargins(7,0,7,0);box.setLayoutParams(layout);box.setOnFocusChangeListener((view,focused)->{view.setScaleX(focused?1.08f:1f);view.setScaleY(focused?1.08f:1f);GradientDrawable frame=new GradientDrawable();frame.setColor(Color.rgb(14,12,35));frame.setStroke(focused?5:2,focused?Color.rgb(232,255,24):Color.rgb(0,220,245));frame.setCornerRadius(8);view.setBackground(frame);view.setTranslationZ(focused?18:0);});box.setOnClickListener(view->playUrl(item.title,item.url));if(!item.poster.isEmpty())posterLoader.execute(()->loadPoster(item.poster,poster));return box;}

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

    private void checkUpdates(TextView button){button.setText("UPDATES  •  CHECKING…");new Thread(()->{try{JSONObject release=new JSONObject(get(BuildConfig.GITHUB_RELEASES_API));String latest=release.optString("tag_name").replaceFirst("^v","");boolean newer=compareVersions(latest,BuildConfig.VERSION_NAME)>0;String apk=BuildConfig.GITHUB_RELEASES_URL;JSONArray assets=release.optJSONArray("assets");if(assets!=null)for(int i=0;i<assets.length();i++){JSONObject asset=assets.getJSONObject(i);if("ChannelForgeTV-FireTV.apk".equals(asset.optString("name"))){apk=asset.optString("browser_download_url",apk);break;}}final String downloadUrl=apk;runOnUiThread(()->{button.setText(newer?"DOWNLOAD UPDATE v"+latest+" ✓":"UP TO DATE ✓  v"+BuildConfig.VERSION_NAME);showBanner(newer?"Press Updates again to download the official Fire TV APK":"ChannelForge TV is up to date");if(newer)button.setOnClickListener(v->{try{startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse(downloadUrl)));}catch(Exception e){showBanner(downloadUrl);}});});}catch(Exception e){runOnUiThread(()->{button.setText("UPDATES  •  RETRY");showBanner("Could not reach GitHub Releases");});}}).start();}
    private int compareVersions(String a,String b){String[] x=a.split("\\."),y=b.split("\\.");for(int i=0;i<3;i++){int xv=i<x.length?number(x[i]):0,yv=i<y.length?number(y[i]):0;if(xv!=yv)return Integer.compare(xv,yv);}return 0;}
    private int number(String value){try{return Integer.parseInt(value.replaceAll("[^0-9].*$",""));}catch(Exception ignored){return 0;}}

    private void scheduleLive(long delay) {
        video.removeCallbacks(continueLive);
        video.postDelayed(continueLive, delay);
    }

    private void play(int index) {
        if (channels.isEmpty()) return;
        channelIndex = (index + channels.size()) % channels.size();
        tunedChannel = channels.get(channelIndex);
        liveRetries = 0;
        guide.setVisibility(View.GONE);
        guideVisible = false;
        video.removeCallbacks(continueLive);
        continueLive.run();
        showBanner(String.format(Locale.US, "CH %03d   %s", channelIndex + 1, tunedChannel.name));
    }

    private void playUrl(String title, String url) {
        tunedChannel = null;
        video.removeCallbacks(continueLive);
        guide.setVisibility(View.GONE);
        guideVisible = false;
        video.setVideoURI(Uri.parse(url));
        video.start();
        showBanner("REWIND CINEMA   •   " + title);
    }
    private void showBanner(String message){banner.setText(message);banner.setVisibility(View.VISIBLE);banner.removeCallbacks(hideBanner);banner.postDelayed(hideBanner,4000);} private final Runnable hideBanner=()->banner.setVisibility(View.GONE);

    @Override public boolean onKeyDown(int key, KeyEvent event){if(!guideVisible&&(key==KeyEvent.KEYCODE_DPAD_CENTER||key==KeyEvent.KEYCODE_ENTER||key==KeyEvent.KEYCODE_BACK)){returnToGuide();channelRail.requestFocus();return true;}if(!guideVisible&&(key==KeyEvent.KEYCODE_DPAD_UP||key==KeyEvent.KEYCODE_CHANNEL_UP)){play(channelIndex+1);return true;}if(!guideVisible&&(key==KeyEvent.KEYCODE_DPAD_DOWN||key==KeyEvent.KEYCODE_CHANNEL_DOWN)){play(channelIndex-1);return true;}return super.onKeyDown(key,event);}

    @Override protected void onResume(){super.onResume();if(guide!=null)loadDirectory();}
    @Override protected void onStop() {
        returnToGuide();
        super.onStop();
    }

    private void returnToGuide() {
        tunedChannel = null;
        video.removeCallbacks(continueLive);
        video.stopPlayback();
        guideVisible = true;
        guide.setVisibility(View.VISIBLE);
        banner.setVisibility(View.GONE);
    }
    @Override protected void onDestroy(){if(guide!=null)guide.removeCallbacks(directoryRefresh);posterLoader.shutdownNow();video.stopPlayback();super.onDestroy();}

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
    private static class Channel { final String name,url; Channel(String n,String u){name=n;url=u;} }
    private static class VaultItem { final String title,year,summary,poster,url; VaultItem(String t,String y,String s,String p,String u){title=t;year=y;summary=s;poster=p;url=u;} }
}
