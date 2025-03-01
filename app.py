from flask import Flask, request, jsonify, send_from_directory
from scripts.spotify_youtube_converter import process_playlist, start_download
import os

# Initialize Flask app
app = Flask(__name__, static_folder='public')

# Ensure downloads directory exists
if not os.path.exists('downloads'):
    os.makedirs('downloads')
if not os.path.exists('downloads/temp'):
    os.makedirs('downloads/temp')

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

@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)