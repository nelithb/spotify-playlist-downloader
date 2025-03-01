import os
import json
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from googleapiclient.discovery import build
from urllib.parse import urlparse
import yt_dlp
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import sys
import subprocess
import traceback
import shutil

# Dotenv handling for Railway deployment (if applicable)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# Directory setup
DOWNLOADS_DIR = 'downloads'
TEMP_DIR = os.path.join(DOWNLOADS_DIR, 'temp')
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)



def log(message):
    """Log message to stderr for server to capture"""
    print(message, file=sys.stderr, flush=True)


def get_ffmpeg_paths():
    """Quickly locate FFmpeg and FFprobe using the system PATH or environment variables"""
    ffmpeg = os.getenv("FFMPEG_PATH", shutil.which("ffmpeg"))
    ffprobe = os.getenv("FFPROBE_PATH", shutil.which("ffprobe"))

    if ffmpeg and ffprobe:
        log(f"Found FFmpeg at: {ffmpeg}")
        log(f"Found FFprobe at: {ffprobe}")
        return ffmpeg, ffprobe
    else:
        log("FFmpeg or FFprobe not found via PATH or environment variables.")
        sys.exit(1)  # Or handle error appropriately for your server application


# Get FFmpeg paths at the start of the script
FFMPEG_PATH, FFPROBE_PATH = get_ffmpeg_paths()




class CustomLogger:
    """Custom logger for yt-dlp"""
    def debug(self, msg):
        log(f"Debug: {msg}")

    def warning(self, msg):
        log(f"Warning: {msg}")

    def error(self, msg):
        log(f"Error: {msg}")


def get_spotify_playlist_tracks(playlist_id):
    """Fetch tracks from Spotify playlist"""
    log("Fetching Spotify playlist tracks...")
    client_credentials_manager = SpotifyClientCredentials(
        client_id=os.getenv('SPOTIPY_CLIENT_ID'),
        client_secret=os.getenv('SPOTIPY_CLIENT_SECRET')
    )
    sp = spotipy.Spotify(client_credentials_manager=client_credentials_manager)

    playlist_info = sp.playlist(playlist_id)
    playlist_name = playlist_info['name']
    playlist_owner = playlist_info['owner']['display_name']
    results = sp.playlist_tracks(playlist_id)
    tracks = results['items']
    songs = []

    for track in tracks:
        if track['track']:
            song = track['track']['name']
            artist = track['track']['artists'][0]['name'] if track['track']['artists'] else 'Unknown Artist'
            duration_ms = track['track']['duration_ms']
            duration_min = round(duration_ms / 60000, 2)
            songs.append({
                "title": song,
                "artist": artist,
                "duration": duration_min
            })

    playlist_info = {
        "name": playlist_name,
        "owner": playlist_owner,
        "track_count": len(songs)
    }
    log(f"Found {len(songs)} tracks in the Spotify playlist: {playlist_name}")
    return songs, playlist_info


def get_youtube_links(songs):
    """Get YouTube URLs for songs"""
    log("Fetching YouTube links...")
    youtube = build('youtube', 'v3', developerKey=os.getenv('YOUTUBE_API_KEY'))
    youtube_links = []

    for song in songs:
        try:
            query = f"{song['artist']} - {song['title']}"
            request = youtube.search().list(
                part="id,snippet",
                q=query,
                type="video",
                maxResults=1
            )
            response = request.execute()
            if response['items']:
                video_id = response['items'][0]['id']['videoId']
                youtube_links.append(f"https://www.youtube.com/watch?v={video_id}")
            else:
                youtube_links.append("No result found")
                log(f"No YouTube result found for: {query}")
        except Exception as e:
            log(f"Error searching for {query}: {str(e)}")
            youtube_links.append("Error: Could not search")

    return youtube_links


def check_ffmpeg():  # Might not be needed if you do the check in get_ffmpeg_paths()
    try:
        result = subprocess.run([FFMPEG_PATH, '-version'], capture_output=True)
        log(f"FFmpeg check successful: {result.stdout.decode().strip()}")
        return True
    except Exception as e:
        log(f"FFmpeg check failed: {str(e)}")
        return False



def download_song(song, url):
    try:
        log(f"Starting download for: {song['title']}")

        safe_title = "".join(x for x in f"{song['artist']} - {song['title']}" if x.isalnum() or x in "- ")
        output_path = os.path.join(TEMP_DIR, f'{safe_title}.%(ext)s')

        ydl_opts = {
            'format': 'bestaudio/best',
            **({"ffmpeg_location": FFMPEG_PATH} if FFMPEG_PATH and os.path.isabs(FFMPEG_PATH) else {}),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': output_path,
            'quiet': False,
            'no_warnings': False,
            'logger': CustomLogger(),
            'writethumbnail': False,
            'postprocessor_args': [
                '-metadata', f'title={song["title"]}',
                '-metadata', f'artist={song["artist"]}'
            ],
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        final_filename = f"{safe_title}.mp3"
        final_path = os.path.join(TEMP_DIR, final_filename)

        if os.path.exists(final_path):
            log(f"Successfully downloaded: {final_filename}")
            return final_filename
        else:
            log(f"File not found after download: {final_path}")
            return None

    except Exception as e:
        log(f"Error in download_song: {str(e)}")
        log(traceback.format_exc())
        return None


def download_playlist(songs, links):
    """Download all songs and create ZIP file"""
    log("Starting download_playlist function...")
    downloaded_files = []
    successful_downloads = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_song = {
            executor.submit(download_song, song, link): song
            for song, link in zip(songs, links) if link != "No result found" and not link.startswith("Error:")
        }

        for future in as_completed(future_to_song):
            song = future_to_song[future]

            file = future.result()
            if file:
                downloaded_files.append(file)
                successful_downloads.append({
                    "title": song['title'],
                    "artist": song['artist'],
                    "duration": song['duration']
                })


    if not downloaded_files:
        log("No files were downloaded successfully")
        return None, successful_downloads

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_filename = f'playlist_downloads_{timestamp}.zip'
    zip_path = os.path.join(DOWNLOADS_DIR, zip_filename)

    try:
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for file in downloaded_files:
                file_path = os.path.join(TEMP_DIR, file)
                if os.path.exists(file_path):
                    zipf.write(file_path, file)
                    os.remove(file_path)
                else:
                    log(f"File not found: {file_path}")
    except Exception as e:
        log(f"Error creating zip file: {str(e)}")
        return None, successful_downloads

    return zip_filename, successful_downloads



def extract_playlist_id(playlist_url):
    """Extract Spotify playlist ID from URL"""
    try:
        parsed_url = urlparse(playlist_url)
        path_parts = parsed_url.path.split('/')
        if 'playlist' in path_parts:
            return path_parts[path_parts.index('playlist') + 1]
    except Exception:
        return None
    return None


def process_playlist(playlist_url):
    """Process a playlist URL and return the result"""
    try:
        playlist_id = extract_playlist_id(playlist_url)
        if not playlist_id:
            return {"success": False, "error": "Invalid Spotify playlist URL"}

        songs, playlist_info = get_spotify_playlist_tracks(playlist_id)
        if not songs:
            return {"success": False, "error": "No tracks found in playlist"}

        return {
            "success": True,
            "phase": "info",
            "playlist_info": playlist_info,
            "songs": songs
        }

    except Exception as e:
        log(f"Error processing playlist: {str(e)}")
        log(traceback.format_exc())
        return {"success": False, "error": str(e)}



def start_download(songs):
    """Start the actual download process after confirmation"""
    try:
        youtube_links = get_youtube_links(songs)
        errors = [link for link in youtube_links if link.startswith("Error:") or link == "No result found"]

        zip_filename, successful_downloads = download_playlist(songs, youtube_links)

        if zip_filename:
            zip_path = os.path.join(DOWNLOADS_DIR, zip_filename)
            if os.path.exists(zip_path) and os.path.getsize(zip_path) > 0:
                return {
                    "success": True,
                    "phase": "complete",
                    "songCount": len(successful_downloads),
                    "zipFile": zip_filename,
                    "errors": errors,
                    "downloadedSongs": successful_downloads
                }


        return {
            "success": False,
            "error": "Failed to create zip file or no songs were downloaded",
            "songCount": len(songs),

            "errors": errors,
            "downloadedSongs": successful_downloads
        }


    except Exception as e:
        log(f"Error during download: {str(e)}")
        log(traceback.format_exc())
        return {"success": False, "error": str(e)}



def cleanup_temp_files():
    """Clean up temporary files"""
    for file in os.listdir(TEMP_DIR):
        try:
            file_path = os.path.join(TEMP_DIR, file)
            if os.path.isfile(file_path):
                os.remove(file_path)
        except Exception as e:
            log(f"Error cleaning up {file}: {str(e)}")


if __name__ == "__main__":
    print("This script is designed to be imported as a module")