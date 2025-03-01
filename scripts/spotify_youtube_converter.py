import os
import json
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from googleapiclient.discovery import build
from dotenv import load_dotenv
from urllib.parse import urlparse
import yt_dlp
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import sys
import subprocess
import traceback
import shutil

# Add dotenv handling for Railway deployment
try:
    from dotenv import load_dotenv
    load_dotenv()  # this will only run locally
except ImportError:
    pass  # when on Railway, we don't need dotenv

# Directory Setup
DOWNLOADS_DIR = 'downloads'
TEMP_DIR = os.path.join(DOWNLOADS_DIR, 'temp')

# Define logging before any functions use it
def log(message):
    """Log message to stderr for server to capture"""
    print(message, file=sys.stderr, flush=True)

# Define global variables BEFORE any functions use them as globals.  Initialization is key.
FFMPEG_PATH = None  # Initialize to None 
FFPROBE_PATH = None # Initialize to None

# Now define function that may alter these globals
def find_and_set_ffmpeg():
    """Find and set FFmpeg paths"""
    log("Searching for FFmpeg...")
    
    # Try using the PATH
    ffmpeg_path = shutil.which('ffmpeg')
    ffprobe_path = shutil.which('ffprobe')
    
    if ffmpeg_path and ffprobe_path:
        log(f"Found FFmpeg in PATH: {ffmpeg_path}")
        log(f"Found FFprobe in PATH: {ffprobe_path}")
        global FFMPEG_PATH, FFPROBE_PATH
        FFMPEG_PATH = ffmpeg_path
        FFPROBE_PATH = ffprobe_path
        return True
    
    # Try common locations
    common_locations = [
        '/usr/bin',
        '/usr/local/bin',
        '/opt/homebrew/bin',
        '/opt/ffmpeg/bin',
        '/app/bin'
    ]
    
    for location in common_locations:
        ffmpeg = os.path.join(location, 'ffmpeg')
        ffprobe = os.path.join(location, 'ffprobe')
        
        if os.path.exists(ffmpeg) and os.path.exists(ffprobe):
            log(f"Found FFmpeg in: {ffmpeg}")
            log(f"Found FFprobe in: {ffprobe}")
            global FFMPEG_PATH, FFPROBE_PATH
            FFMPEG_PATH = ffmpeg
            FFPROBE_PATH = ffprobe
            return True
    
    # Last resort - search common directories
    try:
        result = subprocess.run(["find", "/usr", "-name", "ffmpeg", "-type", "f"], 
                               capture_output=True, text=True, timeout=10)
        paths = result.stdout.strip().split('\n')
        if paths and paths[0]:
            log(f"Found FFmpeg at: {paths[0]}")
            global FFMPEG_PATH
            FFMPEG_PATH = paths[0]
            
            # Try to find ffprobe near ffmpeg
            ffprobe = paths[0].replace('ffmpeg', 'ffprobe')
            if os.path.exists(ffprobe):
                log(f"Found FFprobe at: {ffprobe}")
                global FFPROBE_PATH
                FFPROBE_PATH = ffprobe
                return True
    except Exception as e:
        log(f"Error searching filesystem for FFmpeg: {e}")
    
    log("Could not find FFmpeg and FFprobe!")
    return False

# Call find_and_set_ffmpeg AFTER defining it.
find_and_set_ffmpeg()





# Create directories *after* setting paths.
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)


class CustomLogger:
    """Custom logger for yt-dlp that redirects to our logging"""

    def debug(self, msg):
        log(f"Debug: {msg}")  # Now logging debug messages

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
    
    # Get playlist details
    playlist_info = sp.playlist(playlist_id)
    playlist_name = playlist_info['name']
    playlist_owner = playlist_info['owner']['display_name']
    results = sp.playlist_tracks(playlist_id)
    tracks = results['items']
    
    songs = []
    for track in tracks:
        if track['track']:  # Check if track exists
            song = track['track']['name']
            artist = track['track']['artists'][0]['name'] if track['track']['artists'] else 'Unknown Artist'
            duration_ms = track['track']['duration_ms']
            duration_min = round(duration_ms / 60000, 2)  # Convert to minutes
            
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

def check_ffmpeg():
    """Check if FFmpeg is available"""
    try:
        # Check the environment variable path first
        result = subprocess.run([FFMPEG_PATH, '-version'], capture_output=True)
        log(f"FFmpeg found at: {FFMPEG_PATH}")
        return True
    except Exception as e:
        log(f"FFmpeg check failed: {str(e)}")
        return False

def download_song(song, url):
    try:
        log(f"Starting download for: {song['title']}")
        
        # Sanitize filename
        safe_title = "".join(x for x in f"{song['artist']} - {song['title']}" if x.isalnum() or x in "- ")
        output_path = os.path.join(TEMP_DIR, f'{safe_title}.%(ext)s')
        
        # Use the environment variable path
        ydl_opts = {
            'format': 'bestaudio/best',
            **({"ffmpeg_location": FFMPEG_PATH} if os.path.isabs(FFMPEG_PATH) else {}),
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
        
        # Remove the ffmpeg path detection loop since we're using environment variables
        
        log(f"Starting YouTube-DL with options: {ydl_opts}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            log(f"Downloading from URL: {url}")
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
    log("Starting download_playlist function...")  # Add this
    downloaded_files = []
    successful_downloads = []

    # Process downloads concurrently
    with ThreadPoolExecutor(max_workers=5) as executor:
        log(f"Processing {len(songs)} songs...")  # Add this
        future_to_song = {
            executor.submit(download_song, song, link): song
            for song, link in zip(songs, links)
            if link != "No result found" and not link.startswith("Error:")
        }
        
        for future in as_completed(future_to_song):
            song = future_to_song[future]
            log(f"Processing download for: {song['title']}")  # Add this
            file = future.result()
            if file:
                log(f"Successfully downloaded: {file}")  # Add this
                downloaded_files.append(file)
                successful_downloads.append({
                    "title": song['title'],
                    "artist": song['artist'],
                    "duration": song['duration']
                })
            else:
                log(f"Failed to download: {song['title']}")  # Add this

    if not downloaded_files:
        log("No files were downloaded successfully")  # Add this
        return None, successful_downloads

    # Create ZIP file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_filename = f'playlist_downloads_{timestamp}.zip'
    zip_path = os.path.join(DOWNLOADS_DIR, zip_filename)
    log(f"Creating zip file at: {zip_path}")  # Add this

    try:
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for file in downloaded_files:
                file_path = os.path.join(TEMP_DIR, file)
                log(f"Adding to zip: {file_path}")  # Add this
                if os.path.exists(file_path):
                    zipf.write(file_path, file)
                    os.remove(file_path)  # Clean up temp file
                else:
                    log(f"File not found: {file_path}")  # Add this
    except Exception as e:
        log(f"Error creating zip file: {str(e)}")  # Add this
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

        # Get tracks from Spotify
        songs, playlist_info = get_spotify_playlist_tracks(playlist_id)
        if not songs:
            return {"success": False, "error": "No tracks found in playlist"}

        # Return playlist info and songs for confirmation
        return {
            "success": True,
            "phase": "info",
            "playlist_info": playlist_info,
            "songs": songs
        }

    except Exception as e:
        log(f"Error processing playlist: {str(e)}")
        log(traceback.format_exc())
        return {
            "success": False,
            "error": str(e)
        }

def start_download(songs):
    """Start the actual download process after confirmation"""
    try:
        # Get YouTube links
        youtube_links = get_youtube_links(songs)
        errors = [link for link in youtube_links if link.startswith("Error:") or link == "No result found"]

        # Download songs and create ZIP
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
        return {
            "success": False,
            "error": str(e)
        }

# Cleanup function to remove old files
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