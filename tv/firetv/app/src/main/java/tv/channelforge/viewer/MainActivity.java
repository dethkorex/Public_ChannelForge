package tv.channelforge.viewer;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
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
    private VideoView video;
    private LinearLayout guide, creatorRail, channelRail;
    private TextView status, banner;
    private int channelIndex = 0;
    private boolean guideVisible = true;

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
        TextView brand = text("CHANNELFORGE  /  LIVE CREATOR TV", 18, true); brand.setTextColor(Color.rgb(103,240,194)); guide.addView(brand);
        status = text("Finding published creators…", 13, false); status.setTextColor(Color.LTGRAY); guide.addView(status);
        LinearLayout columns = new LinearLayout(this); columns.setOrientation(LinearLayout.HORIZONTAL); guide.addView(columns, new LinearLayout.LayoutParams(-1, 0, 1));
        creatorRail = new LinearLayout(this); creatorRail.setOrientation(LinearLayout.VERTICAL); ScrollView creatorScroll = new ScrollView(this); creatorScroll.addView(creatorRail); columns.addView(creatorScroll, new LinearLayout.LayoutParams(0,-1,0.34f));
        channelRail = new LinearLayout(this); channelRail.setOrientation(LinearLayout.VERTICAL); ScrollView channelScroll = new ScrollView(this); channelScroll.addView(channelRail); columns.addView(channelScroll, new LinearLayout.LayoutParams(0,-1,0.66f));
        TextView help = text("↑↓ channel surf   •   OK guide/watch   •   ←→ creators   •   REWIND last channel", 12, false); help.setTextColor(Color.GRAY); guide.addView(help);
        root.addView(guide, new FrameLayout.LayoutParams(-1,-1));
        banner = text("", 18, true); banner.setBackgroundColor(Color.argb(220,9,18,29)); banner.setVisibility(View.GONE); FrameLayout.LayoutParams bp=new FrameLayout.LayoutParams(-1,-2,Gravity.BOTTOM); bp.setMargins(35,0,35,35); root.addView(banner,bp);
        setContentView(root);
        video.setOnErrorListener((player, what, extra) -> { showBanner("Stream unavailable — check the creator server"); return true; });
    }

    private String get(String address) throws Exception {
        HttpURLConnection connection=(HttpURLConnection)new URL(address).openConnection(); connection.setConnectTimeout(10000); connection.setReadTimeout(15000); connection.setRequestProperty("User-Agent","ChannelForgeTV/1.0");
        try(InputStream input=connection.getInputStream()){ return new String(readAll(input), StandardCharsets.UTF_8); }
    }

    private byte[] readAll(InputStream input) throws IOException { ByteArrayOutputStream out=new ByteArrayOutputStream(); byte[] buffer=new byte[8192]; int count; while((count=input.read(buffer))>=0) out.write(buffer,0,count); return out.toByteArray(); }

    private void loadDirectory() {
        new Thread(() -> { try { JSONArray values=new JSONObject(get(BuildConfig.DIRECTORY_URL)).getJSONArray("creators"); for(int i=0;i<values.length();i++){JSONObject o=values.getJSONObject(i); creators.add(new Creator(o.getString("name"),o.optString("description"),o.getString("playlist_url")));} runOnUiThread(this::renderCreators); } catch(Exception error){runOnUiThread(()->status.setText("Directory unavailable. Ask your ChannelForge creator for a pairing URL."));} }).start();
    }

    private TextView focusButton(String label) { TextView button=text(label,16,true); button.setFocusable(true); button.setBackgroundColor(Color.rgb(15,27,42)); button.setOnFocusChangeListener((v, focused)->v.setBackgroundColor(focused?Color.rgb(34,91,90):Color.rgb(15,27,42))); return button; }

    private void renderCreators() { creatorRail.removeAllViews(); status.setText(creators.size()+" creator studios online"); for(Creator creator:creators){TextView button=focusButton(creator.name+"\n"+creator.description); button.setOnClickListener(v->loadCreator(creator)); creatorRail.addView(button,new LinearLayout.LayoutParams(-1,-2));} if(!creators.isEmpty()) loadCreator(creators.get(0)); }

    private void loadCreator(Creator creator) { status.setText("Loading "+creator.name+"…"); new Thread(()->{try{List<Channel> loaded=parseM3u(get(creator.playlist)); runOnUiThread(()->{channels.clear();channels.addAll(loaded);channelIndex=0;renderChannels(creator.name);});}catch(Exception error){runOnUiThread(()->status.setText("Could not load "+creator.name));}}).start(); }

    private List<Channel> parseM3u(String m3u){List<Channel> result=new ArrayList<>();String pending="";for(String raw:m3u.split("\\r?\\n")){String line=raw.trim();if(line.startsWith("#EXTINF:")){int comma=line.lastIndexOf(',');pending=comma>=0?line.substring(comma+1).trim():"Channel";}else if(!line.isEmpty()&&!line.startsWith("#")&&!pending.isEmpty()){result.add(new Channel(pending,line));pending="";}}return result;}

    private void renderChannels(String creator){channelRail.removeAllViews();status.setText(creator+"  •  "+channels.size()+" live channels");for(int i=0;i<channels.size();i++){final int index=i;Channel channel=channels.get(i);TextView button=focusButton(String.format(Locale.US,"%03d   %s\nLIVE NOW  •  Select to watch",i+1,channel.name));button.setOnClickListener(v->play(index));channelRail.addView(button,new LinearLayout.LayoutParams(-1,-2));}}

    private void play(int index){if(channels.isEmpty())return;channelIndex=(index+channels.size())%channels.size();Channel channel=channels.get(channelIndex);video.setVideoURI(Uri.parse(channel.url));video.start();guide.setVisibility(View.GONE);guideVisible=false;showBanner(String.format(Locale.US,"CH %03d   %s",channelIndex+1,channel.name));}
    private void showBanner(String message){banner.setText(message);banner.setVisibility(View.VISIBLE);banner.removeCallbacks(hideBanner);banner.postDelayed(hideBanner,4000);} private final Runnable hideBanner=()->banner.setVisibility(View.GONE);

    @Override public boolean onKeyDown(int key, KeyEvent event){if(key==KeyEvent.KEYCODE_DPAD_CENTER||key==KeyEvent.KEYCODE_ENTER){guideVisible=!guideVisible;guide.setVisibility(guideVisible?View.VISIBLE:View.GONE);return true;}if(!guideVisible&&(key==KeyEvent.KEYCODE_DPAD_UP||key==KeyEvent.KEYCODE_CHANNEL_UP)){play(channelIndex+1);return true;}if(!guideVisible&&(key==KeyEvent.KEYCODE_DPAD_DOWN||key==KeyEvent.KEYCODE_CHANNEL_DOWN)){play(channelIndex-1);return true;}return super.onKeyDown(key,event);}

    private static class Creator { final String name,description,playlist; Creator(String n,String d,String p){name=n;description=d;playlist=p;} }
    private static class Channel { final String name,url; Channel(String n,String u){name=n;url=u;} }
}
