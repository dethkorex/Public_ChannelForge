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

public class MainActivity extends Activity {
    private final List<Creator> creators = new ArrayList<>();
    private final List<Channel> channels = new ArrayList<>();
    private final List<VaultItem> vaultItems = new ArrayList<>();
    private VideoView video;
    private LinearLayout guide, creatorRail, channelRail;
    private TextView status, banner;
    private int channelIndex = 0;
    private boolean guideVisible = true;
    private static final String PREFS = "channel_forge_tv";
    private static final String PAIRED_CREATOR = "paired_creator_url";

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().getDecorView().setSystemUiVisibility(5894);
        buildUi();
        loadDirectory();
    }

    private TextView text(String value, int size, boolean bold) {
        TextView view = new TextView(this); view.setText(value); view.setTextColor(Color.WHITE); view.setTextSize(size);
        view.setPadding(22, 13, 22, 13); if (bold) view.setTypeface(Typeface.DEFAULT_BOLD); return view;
    }

    private void buildUi() {
        FrameLayout root = new FrameLayout(this); root.setBackgroundColor(Color.rgb(5, 10, 17));
        video = new VideoView(this); root.addView(video, new FrameLayout.LayoutParams(-1, -1));
        guide = new LinearLayout(this); guide.setOrientation(LinearLayout.VERTICAL); guide.setPadding(34, 25, 34, 25); guide.setBackgroundColor(Color.argb(238, 5, 10, 17));
        LinearLayout header=new LinearLayout(this);header.setGravity(Gravity.CENTER_VERTICAL);TextView brand = text("CHANNELFORGE TV   /   REWIND CINEMA   /   LIVE GUIDE", 20, true); brand.setTextColor(Color.rgb(255,232,45));header.addView(brand,new LinearLayout.LayoutParams(0,-2,1));TextView pair=focusButton("PAIR CREATOR");pair.setOnClickListener(v->showPairingDialog());header.addView(pair);TextView updates=focusButton("UPDATES  •  v"+BuildConfig.VERSION_NAME);updates.setOnClickListener(v->checkUpdates(updates));header.addView(updates);guide.addView(header);
        status = text("Finding published creators…", 13, false); status.setTextColor(Color.LTGRAY); guide.addView(status);
        LinearLayout columns = new LinearLayout(this); columns.setOrientation(LinearLayout.HORIZONTAL); guide.addView(columns, new LinearLayout.LayoutParams(-1, 0, 1));
        creatorRail = new LinearLayout(this); creatorRail.setOrientation(LinearLayout.VERTICAL); creatorRail.setPadding(0,0,18,0); ScrollView creatorScroll = new ScrollView(this); creatorScroll.addView(creatorRail); columns.addView(creatorScroll, new LinearLayout.LayoutParams(0,-1,0.27f));
        channelRail = new LinearLayout(this); channelRail.setOrientation(LinearLayout.VERTICAL); ScrollView channelScroll = new ScrollView(this); channelScroll.addView(channelRail); columns.addView(channelScroll, new LinearLayout.LayoutParams(0,-1,0.73f));
        TextView help = text("↑↓ channel surf   •   OK guide/watch   •   ←→ creators   •   REWIND last channel", 12, false); help.setTextColor(Color.GRAY); guide.addView(help);
        root.addView(guide, new FrameLayout.LayoutParams(-1,-1));
        banner = text("", 18, true); banner.setBackgroundColor(Color.argb(220,9,18,29)); banner.setVisibility(View.GONE); FrameLayout.LayoutParams bp=new FrameLayout.LayoutParams(-1,-2,Gravity.BOTTOM); bp.setMargins(35,0,35,35); root.addView(banner,bp);
        setContentView(root);
        video.setOnErrorListener((player, what, extra) -> { showBanner("Stream unavailable — check the creator server"); return true; });
    }

    private String get(String address) throws Exception {
        HttpURLConnection connection=(HttpURLConnection)new URL(address).openConnection(); connection.setConnectTimeout(10000); connection.setReadTimeout(15000); connection.setRequestProperty("Accept","application/json, application/x-mpegURL, */*"); connection.setRequestProperty("User-Agent","ChannelForgeTV/"+BuildConfig.VERSION_NAME);
        int code=connection.getResponseCode(); if(code<200||code>=300)throw new IOException("HTTP "+code+" from "+address);
        try(InputStream input=connection.getInputStream()){ return new String(readAll(input), StandardCharsets.UTF_8); }
    }

    private byte[] readAll(InputStream input) throws IOException { ByteArrayOutputStream out=new ByteArrayOutputStream(); byte[] buffer=new byte[8192]; int count; while((count=input.read(buffer))>=0) out.write(buffer,0,count); return out.toByteArray(); }

    private void loadDirectory() {
        status.setText("Scanning the public ChannelForge directory…");
        new Thread(() -> {
            creators.clear();
            String paired=getSharedPreferences(PREFS,MODE_PRIVATE).getString(PAIRED_CREATOR,"");
            if(!paired.isEmpty())try{creators.add(readPairedCreator(paired));}catch(Exception ignored){}
            Exception last=null;
            for(String address:new String[]{BuildConfig.DIRECTORY_URL,BuildConfig.DIRECTORY_FALLBACK_URL}){
                try{
                    JSONArray values=new JSONObject(get(address)).getJSONArray("creators");
                    for(int i=0;i<values.length();i++){
                        JSONObject o=values.getJSONObject(i); String playlist=o.getString("playlist_url");
                        if(!hasCreator(playlist))creators.add(new Creator(o.getString("name"),o.optString("description"),playlist,o.optString("vault_url")));
                    }
                    if(!creators.isEmpty())break;
                }catch(Exception error){last=error;}
            }
            Exception failure=last;
            if(!creators.isEmpty())runOnUiThread(this::renderCreators);else runOnUiThread(()->renderDirectoryRecovery(failure));
        }).start();
    }

    private boolean hasCreator(String playlist){for(Creator creator:creators)if(creator.playlist.equals(playlist))return true;return false;}

    private Creator readPairedCreator(String raw) throws Exception {
        String base=raw.trim().replaceAll("/+$","");
        if(base.endsWith("/api/public/channels"))base=base.substring(0,base.length()-"/api/public/channels".length());
        if(!base.startsWith("https://"))throw new IOException("Pairing URLs must use HTTPS");
        JSONObject state=new JSONObject(get(base+"/api/public/channels"));
        if(!state.has("channels"))throw new IOException("That URL is not a ChannelForge Creator");
        String host=Uri.parse(base).getHost();
        return new Creator(host==null?"Paired Creator":host,"Paired directly with this Fire TV",base+"/exports/channels.m3u",base+"/api/public/vault");
    }

    private void renderDirectoryRecovery(Exception error){
        creatorRail.removeAllViews();channelRail.removeAllViews();
        status.setText("Public directory is offline. Retry it or pair this TV directly with a Creator.");
        TextView retry=focusButton("↻  RETRY PUBLIC DIRECTORY");retry.setOnClickListener(v->loadDirectory());creatorRail.addView(retry,new LinearLayout.LayoutParams(-1,-2));
        TextView pair=focusButton("＋  ENTER CREATOR PAIRING URL");pair.setOnClickListener(v->showPairingDialog());channelRail.addView(pair,new LinearLayout.LayoutParams(-1,-2));
        TextView detail=text(error==null?"No creator studios are online yet.":"The public service could not be reached. A direct pairing still works.",13,false);detail.setTextColor(Color.LTGRAY);channelRail.addView(detail);
        retry.requestFocus();
    }

    private void showPairingDialog(){
        EditText input=new EditText(this);input.setSingleLine(true);input.setHint("https://your-studio.trycloudflare.com");input.setText(getSharedPreferences(PREFS,MODE_PRIVATE).getString(PAIRED_CREATOR,""));input.setSelectAllOnFocus(true);input.setPadding(30,18,30,18);
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("Pair ChannelForge Creator").setMessage("In Creator, open System → Publish worldwide and copy its HTTPS pairing URL.").setView(input).setNegativeButton("CANCEL",null).setNeutralButton("FORGET",(d,w)->{getSharedPreferences(PREFS,MODE_PRIVATE).edit().remove(PAIRED_CREATOR).apply();loadDirectory();}).setPositiveButton("PAIR",null).create();
        dialog.setOnShowListener(ignored->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{String address=input.getText().toString().trim();if(address.isEmpty()){input.setError("Enter the Creator HTTPS URL");return;}dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(false);status.setText("Pairing with Creator…");new Thread(()->{try{Creator creator=readPairedCreator(address);getSharedPreferences(PREFS,MODE_PRIVATE).edit().putString(PAIRED_CREATOR,address.replaceAll("/+$","")).apply();runOnUiThread(()->{dialog.dismiss();creators.clear();creators.add(creator);renderCreators();});}catch(Exception error){runOnUiThread(()->{dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(true);input.setError("Could not reach this Creator. Check that Creator is open and the URL begins with https://");});}}).start();}));
        dialog.show();
    }

    private TextView focusButton(String label) { TextView button=text(label,16,true); button.setFocusable(true); button.setBackgroundColor(Color.rgb(18,18,35)); button.setOnFocusChangeListener((v, focused)->{v.setBackgroundColor(focused?Color.rgb(76,54,18):Color.rgb(18,18,35));v.setScaleX(focused?1.02f:1f);v.setScaleY(focused?1.02f:1f);}); return button; }

    private void renderCreators() { creatorRail.removeAllViews(); status.setText(creators.size()+" creator studios online"); for(Creator creator:creators){TextView button=focusButton(creator.name+"\n"+creator.description); button.setOnClickListener(v->loadCreator(creator)); creatorRail.addView(button,new LinearLayout.LayoutParams(-1,-2));} if(!creators.isEmpty()) loadCreator(creators.get(0)); }

    private void loadCreator(Creator creator) { status.setText("Loading "+creator.name+"…"); new Thread(()->{try{List<Channel> loaded=parseM3u(get(creator.playlist));List<VaultItem> vault=creator.vault.isEmpty()?new ArrayList<>():parseVault(get(creator.vault)); runOnUiThread(()->{channels.clear();channels.addAll(loaded);vaultItems.clear();vaultItems.addAll(vault);channelIndex=0;renderChannels(creator.name);});}catch(Exception error){runOnUiThread(()->status.setText("Could not load "+creator.name));}}).start(); }

    private List<VaultItem> parseVault(String value)throws Exception{List<VaultItem> result=new ArrayList<>();JSONArray items=new JSONObject(value).getJSONArray("items");for(int i=0;i<items.length();i++){JSONObject item=items.getJSONObject(i);result.add(new VaultItem(item.optString("title","Untitled"),item.optString("year"),item.optString("summary"),item.optString("poster_url"),item.getString("stream_url")));}return result;}

    private List<Channel> parseM3u(String m3u){List<Channel> result=new ArrayList<>();String pending="";for(String raw:m3u.split("\\r?\\n")){String line=raw.trim();if(line.startsWith("#EXTINF:")){int comma=line.lastIndexOf(',');pending=comma>=0?line.substring(comma+1).trim():"Channel";}else if(!line.isEmpty()&&!line.startsWith("#")&&!pending.isEmpty()){result.add(new Channel(pending,line));pending="";}}return result;}

    private void renderChannels(String creator){channelRail.removeAllViews();status.setText(creator+"  •  "+channels.size()+" live channels  •  "+vaultItems.size()+" movies");TextView liveTitle=text("▼ LIVE CHANNELS",14,true);liveTitle.setTextColor(Color.rgb(80,235,255));channelRail.addView(liveTitle);for(int i=0;i<channels.size();i++){final int index=i;Channel channel=channels.get(i);TextView button=focusButton(String.format(Locale.US,"%03d   %s\nLIVE NOW  •  Select to watch",i+1,channel.name));button.setOnClickListener(v->play(index));channelRail.addView(button,new LinearLayout.LayoutParams(-1,-2));}TextView vaultTitle=text("REWIND CINEMA   •   BROWSE LEFT / RIGHT THROUGH MOVIES",15,true);vaultTitle.setTextColor(Color.rgb(255,62,184));channelRail.addView(vaultTitle);LinearLayout shelf=new LinearLayout(this);shelf.setOrientation(LinearLayout.HORIZONTAL);shelf.setPadding(8,10,8,20);for(VaultItem item:vaultItems)shelf.addView(vaultBox(item));HorizontalScrollView scroll=new HorizontalScrollView(this);scroll.setHorizontalScrollBarEnabled(false);scroll.addView(shelf);channelRail.addView(scroll,new LinearLayout.LayoutParams(-1,-2));}

    private View vaultBox(VaultItem item){LinearLayout box=new LinearLayout(this);box.setOrientation(LinearLayout.VERTICAL);box.setPadding(8,8,8,8);box.setFocusable(true);GradientDrawable normal=new GradientDrawable();normal.setColor(Color.rgb(14,12,35));normal.setStroke(2,Color.rgb(0,220,245));normal.setCornerRadius(8);box.setBackground(normal);ImageView poster=new ImageView(this);poster.setScaleType(ImageView.ScaleType.CENTER_CROP);poster.setBackgroundColor(Color.rgb(30,15,55));box.addView(poster,new LinearLayout.LayoutParams(185,255));TextView title=text(item.title+(item.year.isEmpty()?"":" ("+item.year+")"),13,true);title.setMaxLines(2);box.addView(title,new LinearLayout.LayoutParams(185,70));LinearLayout.LayoutParams layout=new LinearLayout.LayoutParams(205,350);layout.setMargins(7,0,7,0);box.setLayoutParams(layout);box.setOnFocusChangeListener((view,focused)->{view.setScaleX(focused?1.08f:1f);view.setScaleY(focused?1.08f:1f);GradientDrawable frame=new GradientDrawable();frame.setColor(Color.rgb(14,12,35));frame.setStroke(focused?5:2,focused?Color.rgb(232,255,24):Color.rgb(0,220,245));frame.setCornerRadius(8);view.setBackground(frame);view.setTranslationZ(focused?18:0);});box.setOnClickListener(view->playUrl(item.title,item.url));if(!item.poster.isEmpty())new Thread(()->{try(InputStream input=new URL(item.poster).openStream()){byte[] data=readAll(input);runOnUiThread(()->poster.setImageBitmap(BitmapFactory.decodeByteArray(data,0,data.length)));}catch(Exception ignored){}}).start();return box;}

    private void checkUpdates(TextView button){button.setText("UPDATES  •  CHECKING…");new Thread(()->{try{JSONObject release=new JSONObject(get(BuildConfig.GITHUB_RELEASES_API));String latest=release.optString("tag_name").replaceFirst("^v","");boolean newer=compareVersions(latest,BuildConfig.VERSION_NAME)>0;String apk=BuildConfig.GITHUB_RELEASES_URL;JSONArray assets=release.optJSONArray("assets");if(assets!=null)for(int i=0;i<assets.length();i++){JSONObject asset=assets.getJSONObject(i);if("ChannelForgeTV-FireTV.apk".equals(asset.optString("name"))){apk=asset.optString("browser_download_url",apk);break;}}final String downloadUrl=apk;runOnUiThread(()->{button.setText(newer?"DOWNLOAD UPDATE v"+latest+" ✓":"UP TO DATE ✓  v"+BuildConfig.VERSION_NAME);showBanner(newer?"Press Updates again to download the official Fire TV APK":"ChannelForge TV is up to date");if(newer)button.setOnClickListener(v->{try{startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse(downloadUrl)));}catch(Exception e){showBanner(downloadUrl);}});});}catch(Exception e){runOnUiThread(()->{button.setText("UPDATES  •  RETRY");showBanner("Could not reach GitHub Releases");});}}).start();}
    private int compareVersions(String a,String b){String[] x=a.split("\\."),y=b.split("\\.");for(int i=0;i<3;i++){int xv=i<x.length?number(x[i]):0,yv=i<y.length?number(y[i]):0;if(xv!=yv)return Integer.compare(xv,yv);}return 0;}
    private int number(String value){try{return Integer.parseInt(value.replaceAll("[^0-9].*$",""));}catch(Exception ignored){return 0;}}

    private void play(int index){if(channels.isEmpty())return;channelIndex=(index+channels.size())%channels.size();Channel channel=channels.get(channelIndex);video.setVideoURI(Uri.parse(channel.url));video.start();guide.setVisibility(View.GONE);guideVisible=false;showBanner(String.format(Locale.US,"CH %03d   %s",channelIndex+1,channel.name));}
    private void playUrl(String title,String url){video.setVideoURI(Uri.parse(url));video.start();guide.setVisibility(View.GONE);guideVisible=false;showBanner("REWIND CINEMA   •   "+title);}
    private void showBanner(String message){banner.setText(message);banner.setVisibility(View.VISIBLE);banner.removeCallbacks(hideBanner);banner.postDelayed(hideBanner,4000);} private final Runnable hideBanner=()->banner.setVisibility(View.GONE);

    @Override public boolean onKeyDown(int key, KeyEvent event){if(key==KeyEvent.KEYCODE_DPAD_CENTER||key==KeyEvent.KEYCODE_ENTER){guideVisible=!guideVisible;guide.setVisibility(guideVisible?View.VISIBLE:View.GONE);return true;}if(!guideVisible&&(key==KeyEvent.KEYCODE_DPAD_UP||key==KeyEvent.KEYCODE_CHANNEL_UP)){play(channelIndex+1);return true;}if(!guideVisible&&(key==KeyEvent.KEYCODE_DPAD_DOWN||key==KeyEvent.KEYCODE_CHANNEL_DOWN)){play(channelIndex-1);return true;}return super.onKeyDown(key,event);}

    private static class Creator { final String name,description,playlist,vault; Creator(String n,String d,String p,String v){name=n;description=d;playlist=p;vault=v;} }
    private static class Channel { final String name,url; Channel(String n,String u){name=n;url=u;} }
    private static class VaultItem { final String title,year,summary,poster,url; VaultItem(String t,String y,String s,String p,String u){title=t;year=y;summary=s;poster=p;url=u;} }
}
