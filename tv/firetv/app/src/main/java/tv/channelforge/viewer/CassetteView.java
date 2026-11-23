package tv.channelforge.viewer;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.view.View;

/** Projects the six faces of a VHS box and textures its front with the movie artwork. */
final class CassetteView extends View {
    private static final float WIDTH = 360f, HEIGHT = 182f, DEPTH = 34f;
    private static final int[][] FACES = {
            {0, 1, 2, 3}, {1, 5, 6, 2}, {3, 2, 6, 7},
            {4, 7, 6, 5}, {0, 3, 7, 4}, {0, 4, 5, 1}
    };
    private static final int[] COLORS = {
            0xff3d284a, 0xff150f24, 0xff644657, 0xff21172c, 0xff1d1228, 0xff745363
    };
    private static final float[] FRONT = {0, 0, WIDTH, 0, WIDTH, HEIGHT, 0, HEIGHT};

    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
    private final Path outline = new Path();
    private final Matrix artwork = new Matrix();
    private final float[] points = new float[16], corners = new float[8];
    private final RectF artBounds = new RectF(8, 8, 134, HEIGHT - 8);
    private final String title;
    private Bitmap poster;
    private float position;

    CassetteView(Context context, String title) {
        super(context);
        this.title = title;
        paint.setTypeface(Typeface.DEFAULT_BOLD);
    }

    void setPoster(Bitmap poster) {
        this.poster = poster;
        invalidate();
    }

    void setPosition(float position) {
        this.position = position;
        invalidate();
    }

    @Override protected void onDraw(Canvas canvas) {
        if (position >= .94f) return;
        float scale = Math.min(Math.min(getWidth() / 790f, getHeight() / 490f),
                getResources().getDisplayMetrics().density * .82f);
        float pitch = (float) Math.toRadians(19 + 69 * position);
        float yaw = (float) Math.toRadians(31 * (1 - position));
        float sinX = (float) Math.sin(pitch), cosX = (float) Math.cos(pitch);
        float sinY = (float) Math.sin(yaw), cosY = (float) Math.cos(yaw);
        for (int i = 0; i < 8; i++) {
            float x = (i % 4 == 0 || i % 4 == 3 ? -1 : 1) * WIDTH / 2;
            float y = (i % 4 < 2 ? -1 : 1) * HEIGHT / 2;
            float z = (i < 4 ? 1 : -1) * DEPTH / 2;
            float rx = x * cosY - z * sinY, rz = x * sinY + z * cosY;
            float ry = y * cosX - rz * sinX, depth = y * sinX + rz * cosX;
            float perspective = scale * 570f / (570f - depth + position * 82f);
            points[i * 2] = getWidth() * .5f + (rx + (1 - position) * 24) * perspective;
            points[i * 2 + 1] = getHeight() * .5f + ry * perspective + (1 - position) * getHeight() * .23f;
        }
        for (int i = 0; i < FACES.length; i++) {
            int[] face = FACES[i];
            for (int j = 0; j < 4; j++) {
                corners[j * 2] = points[face[j] * 2];
                corners[j * 2 + 1] = points[face[j] * 2 + 1];
            }
            float cross = (corners[2] - corners[0]) * (corners[5] - corners[1])
                    - (corners[3] - corners[1]) * (corners[4] - corners[0]);
            if (cross <= 0) continue;
            outline.rewind();
            outline.moveTo(corners[0], corners[1]);
            for (int j = 2; j < corners.length; j += 2) outline.lineTo(corners[j], corners[j + 1]);
            outline.close();
            paint.setColor(COLORS[i]);
            canvas.drawPath(outline, paint);
            if (i == 0) drawArtwork(canvas);
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(Math.max(1, scale * 1.4f));
            paint.setColor(0xffbd9678);
            canvas.drawPath(outline, paint);
            paint.setStyle(Paint.Style.FILL);
        }
    }

    private void drawArtwork(Canvas canvas) {
        if (!artwork.setPolyToPoly(FRONT, 0, corners, 0, 4)) return;
        int layer = canvas.save();
        canvas.concat(artwork);
        paint.setColor(0xff1a1329);
        canvas.drawRect(artBounds, paint);
        if (poster != null) {
            paint.setColor(Color.WHITE);
            canvas.drawBitmap(poster, null, artBounds, paint);
        }
        paint.setColor(0xffff629e);
        canvas.drawRect(138, 8, 140, HEIGHT - 8, paint);
        paint.setColor(0xffa0f4ff);
        paint.setTextSize(13);
        canvas.drawText("REWIND CINEMA", 152, 34, paint);
        paint.setColor(0xffffe6ba);
        paint.setTextSize(25);
        int split = Math.min(title.length(), paint.breakText(title, true, 191, null));
        if (split < title.length()) {
            int space = title.lastIndexOf(' ', split);
            if (space > 0) split = space;
        }
        canvas.drawText(title, 0, split, 152, 82, paint);
        if (split < title.length()) {
            int start = title.charAt(split) == ' ' ? split + 1 : split;
            int end = start + paint.breakText(title, start, title.length(), true, 191, null);
            canvas.drawText(title, start, end, 152, 111, paint);
        }
        paint.setColor(0xfff6c281);
        paint.setTextSize(15);
        canvas.drawText("CF HOME VIDEO  •  VHS", 152, 152, paint);
        canvas.restoreToCount(layer);
    }
}
