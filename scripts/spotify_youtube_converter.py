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


def download_song(song, url):
    try:
        log(f"Starting download for: {song['title']}")
        
        # Try to find FFmpeg and FFprobe in common locations
        ffmpeg_locations = [
            "ffmpeg",  # Look in PATH
            "/usr/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/nix/store/*/ffmpeg/bin/ffmpeg"  # Nixpacks often installs here
        ]
        
        ffprobe_locations = [
            "ffprobe",  # Look in PATH
            "/usr/bin/ffprobe",
            "/usr/local/bin/ffprobe",
            "/nix/store/*/ffmpeg/bin/ffprobe"  # Nixpacks often installs here
        ]
        
        # Log available paths
        try:
            import glob
            for pattern in ["/nix/store/*/ffmpeg/bin/ffmpeg", "/nix/store/*/bin/ffmpeg"]:
                matches = glob.glob(pattern)
                for match in matches:
                    log(f"Found potential FFmpeg at: {match}")
            
            # Check if ffmpeg is in PATH
            result = subprocess.run(["which", "ffmpeg"], capture_output=True, text=True)
            if result.returncode == 0:
                log(f"FFmpeg found in PATH at: {result.stdout.strip()}")
            else:
                log("FFmpeg not found in PATH")
                
            # List contents of /nix/store to help diagnose
            if os.path.exists("/nix/store"):
                nix_dirs = os.listdir("/nix/store")
                ffmpeg_dirs = [d for d in nix_dirs if "ffmpeg" in d]
                if ffmpeg_dirs:
                    log(f"Found potential FFmpeg directories in /nix/store: {ffmpeg_dirs}")
        except Exception as e:
            log(f"Error during path discovery: {str(e)}")
        
        # Use the first working FFmpeg we find
        ffmpeg_path = None
        for loc in ffmpeg_locations:
            if '*' in loc:
                # Handle glob patterns
                import glob
                matches = glob.glob(loc)
                for match in matches:
                    try:
                        result = subprocess.run([match, "-version"], capture_output=True, check=True, timeout=5)
                        log(f"Found working FFmpeg at: {match}")
                        ffmpeg_path = match
                        break
                    except Exception:
                        continue
            else:
                try:
                    result = subprocess.run([loc, "-version"], capture_output=True, check=True, timeout=5)
                    log(f"Found working FFmpeg at: {loc}")
                    ffmpeg_path = loc
                    break
                except Exception:
                    continue
        
        if not ffmpeg_path:
            log("ERROR: Could not find a working FFmpeg installation!")
            return None
            
        log(f"Using FFmpeg from: {ffmpeg_path}")
        
        # Rest of your function...
        safe_title = "".join(x for x in f"{song['artist']} - {song['title']}" if x.isalnum() or x in "- ")
        output_path = os.path.join(TEMP_DIR, f'{safe_title}.%(ext)s')
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'ffmpeg_location': os.path.dirname(ffmpeg_path),  # Pass the directory containing ffmpeg
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
        
        log(f"Starting download with yt-dlp, FFmpeg path: {os.path.dirname(ffmpeg_path)}")
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