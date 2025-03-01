from flask import Flask, request, jsonify, send_from_directory
from scripts.spotify_youtube_converter import process_playlist, start_download
import os

app = Flask(__name__, static_folder='public')

@app.route('/')
def home():
    return app.send_static_file('index.html')

@app.route('/convert', methods=['POST'])
def convert():
    data = request.json
    
    if 'url' in data:
        # Initial playlist processing
        result = process_playlist(data['url'])
        return jsonify(result)
    elif 'songs' in data:
        # Start download after confirmation
        result = start_download(data['songs'])
        return jsonify(result)
    else:
        return jsonify({'success': False, 'error': 'Invalid request'})

@app.route('/downloads/<path:filename>')
def download_file(filename):
    return send_from_directory('downloads', filename)

if __name__ == '__main__':
    app.run(debug=True)