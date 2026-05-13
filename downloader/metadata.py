"""Audio metadata embedding via mutagen."""

import urllib.request

try:
    from mutagen import File as MutagenFile
except ImportError:
    MutagenFile = None


def embed_audio_metadata(file_path, info, audio_format):
    """Embed basic metadata and album art into the audio file using mutagen.

    Supports MP3, M4A (MP4), and FLAC. If mutagen is not installed,
    this function will quietly skip embedding.
    """
    thumbnail = info.get("thumbnail") if isinstance(info, dict) else None
    title = info.get("title") if isinstance(info, dict) else None
    uploader = info.get("uploader") or info.get("artist") if isinstance(info, dict) else None
    upload_date = info.get("upload_date") if isinstance(info, dict) else None
    year = None
    if upload_date and len(upload_date) >= 4:
        year = upload_date[:4]

    try:
        audio = MutagenFile(file_path, easy=False)
        if audio is None:
            print("[WARN] mutagen could not open file for tagging")
            return

        # MP3 (ID3)
        if audio_format == "mp3" or file_path.lower().endswith('.mp3'):
            try:
                from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, TDRC
                from mutagen.mp3 import MP3
                mp = MP3(file_path, ID3=ID3)
                try:
                    mp.add_tags()
                except Exception:
                    pass
                if title:
                    mp.tags.add(TIT2(encoding=3, text=title))
                if uploader:
                    mp.tags.add(TPE1(encoding=3, text=uploader))
                if year:
                    mp.tags.add(TDRC(encoding=3, text=year))
                if thumbnail:
                    try:
                        img = urllib.request.urlopen(thumbnail).read()
                        mp.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=img))
                    except Exception:
                        pass
                mp.save(v2_version=3)
            except Exception:
                pass

        # MP4/M4A
        elif audio_format in ("m4a", "mp4") or file_path.lower().endswith('.m4a'):
            try:
                from mutagen.mp4 import MP4, MP4Cover
                m4 = MP4(file_path)
                if title:
                    m4['\xa9nam'] = [title]
                if uploader:
                    m4['\xa9ART'] = [uploader]
                if year:
                    m4['\xa9day'] = [year]
                if thumbnail:
                    try:
                        img = urllib.request.urlopen(thumbnail).read()
                        m4['covr'] = [MP4Cover(img, imageformat=MP4Cover.FORMAT_JPEG)]
                    except Exception:
                        pass
                m4.save()
            except Exception:
                pass

        # FLAC
        elif audio_format == "flac" or file_path.lower().endswith('.flac'):
            try:
                from mutagen.flac import FLAC, Picture
                f = FLAC(file_path)
                if title:
                    f['title'] = title
                if uploader:
                    f['artist'] = uploader
                if year:
                    f['date'] = year
                if thumbnail:
                    try:
                        img = urllib.request.urlopen(thumbnail).read()
                        pic = Picture()
                        pic.data = img
                        pic.type = 3
                        pic.mime = 'image/jpeg'
                        pic.desc = 'Cover'
                        f.add_picture(pic)
                    except Exception:
                        pass
                f.save()
            except Exception:
                pass

        else:
            # Fallback: try to set common easy tags
            try:
                easy = MutagenFile(file_path, easy=True)
                if easy is not None:
                    if title:
                        easy['title'] = title
                    if uploader:
                        easy['artist'] = uploader
                    easy.save()
            except Exception:
                pass

    except Exception:
        print("[WARN] Failed to embed metadata")
        return