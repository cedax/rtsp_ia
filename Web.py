from flask import Flask, render_template_string, jsonify, request, send_file
import os
import json
from datetime import datetime, date
import glob
from pathlib import Path

app = Flask(__name__)

# Configuración de rutas
BASE_PATH = "C:/Users/chlopez/Desktop/CamaraSeguridad"
RECORDINGS_PATH = os.path.join(BASE_PATH, "recordings")

# Template HTML con diseño iOS
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>📹 Video Surveillance</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f2f2f7;
            color: #000;
            line-height: 1.4;
        }
        
        .header {
            background: #fff;
            border-bottom: 1px solid #e5e5ea;
            padding: 20px;
            text-align: center;
            position: sticky;
            top: 0;
            z-index: 100;
        }
        
        .header h1 {
            font-size: 28px;
            font-weight: 700;
            margin-bottom: 10px;
        }
        
        .filters {
            background: #fff;
            margin: 20px;
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        
        .filter-group {
            margin-bottom: 15px;
        }
        
        .filter-group label {
            display: block;
            font-weight: 600;
            margin-bottom: 8px;
            color: #333;
        }
        
        .filter-input {
            width: 100%;
            padding: 12px 16px;
            border: 1px solid #d1d1d6;
            border-radius: 8px;
            font-size: 16px;
            background: #fff;
        }
        
        .filter-input:focus {
            outline: none;
            border-color: #007aff;
            box-shadow: 0 0 0 3px rgba(0,122,255,0.1);
        }
        
        .btn {
            background: #007aff;
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .btn:hover {
            background: #005ecb;
            transform: translateY(-1px);
        }
        
        .btn:active {
            transform: translateY(0);
        }
        
        .videos-container {
            margin: 20px;
        }
        
        .video-card {
            background: #fff;
            border-radius: 12px;
            margin-bottom: 16px;
            overflow: hidden;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            transition: all 0.2s;
        }
        
        .video-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }
        
        .video-header {
            padding: 16px 20px;
            border-bottom: 1px solid #f2f2f7;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .video-title {
            font-weight: 600;
            font-size: 18px;
        }
        
        .video-date {
            color: #8e8e93;
            font-size: 14px;
        }
        
        .expand-icon {
            font-size: 20px;
            color: #8e8e93;
            transition: transform 0.2s;
        }
        
        .video-details {
            display: none;
            padding: 20px;
            background: #f9f9f9;
        }
        
        .video-details.expanded {
            display: block;
        }
        
        .video-details.expanded ~ .video-header .expand-icon {
            transform: rotate(180deg);
        }
        
        .detections-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 12px;
            margin: 16px 0;
        }
        
        .detection-item {
            background: #fff;
            padding: 12px;
            border-radius: 8px;
            text-align: center;
            border: 1px solid #e5e5ea;
        }
        
        .detection-class {
            font-weight: 600;
            margin-bottom: 4px;
        }
        
        .detection-confidence {
            color: #8e8e93;
            font-size: 12px;
        }
        
        .detection-time {
            color: #007aff;
            font-size: 11px;
            margin-top: 4px;
        }
        
        .video-actions {
            display: flex;
            gap: 12px;
            margin-top: 16px;
        }
        
        .btn-secondary {
            background: #8e8e93;
            color: white;
            flex: 1;
        }
        
        .btn-secondary:hover {
            background: #636366;
        }
        
        .video-player {
            width: 100%;
            max-width: 100%;
            border-radius: 8px;
            margin-bottom: 16px;
        }
        
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 12px;
            margin-bottom: 16px;
        }
        
        .stat-item {
            background: #fff;
            padding: 12px;
            border-radius: 8px;
            text-align: center;
            border: 1px solid #e5e5ea;
        }
        
        .stat-value {
            font-size: 20px;
            font-weight: 700;
            color: #007aff;
        }
        
        .stat-label {
            font-size: 12px;
            color: #8e8e93;
            margin-top: 4px;
        }
        
        .loading {
            text-align: center;
            padding: 40px;
            color: #8e8e93;
        }
        
        .no-results {
            text-align: center;
            padding: 40px;
            color: #8e8e93;
        }
        
        @media (max-width: 768px) {
            .header, .filters, .videos-container {
                margin: 10px;
            }
            
            .video-header {
                flex-direction: column;
                align-items: flex-start;
                gap: 8px;
            }
            
            .video-actions {
                flex-direction: column;
            }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>📹 Video Surveillance</h1>
        <p>Sistema de monitoreo con IA</p>
    </div>
    
    <div class="filters">
        <div class="filter-group">
            <label for="dateFilter">📅 Filtrar por fecha:</label>
            <input type="date" id="dateFilter" class="filter-input">
        </div>
        <div class="filter-group">
            <label for="classFilter">🔍 Filtrar por detección:</label>
            <select id="classFilter" class="filter-input">
                <option value="">Todas las detecciones</option>
                <option value="car">🚗 Automóvil</option>
                <option value="person">👤 Persona</option>
                <option value="truck">🚛 Camión</option>
                <option value="motorcycle">🏍️ Motocicleta</option>
                <option value="bicycle">🚲 Bicicleta</option>
                <option value="bear">🐻 Oso</option>
                <option value="traffic light">🚦 Semáforo</option>
                <option value="dog">🐕 Perro</option>
                <option value="cat">🐱 Gato</option>
                <option value="bird">🐦 Pájaro</option>
            </select>
        </div>
        <button class="btn" onclick="filterVideos()">Filtrar</button>
    </div>
    
    <div class="videos-container">
        <div id="loading" class="loading">
            <p>⏳ Cargando videos...</p>
        </div>
        <div id="videos-list"></div>
    </div>

    <script>
        let allVideos = [];
        
        async function loadVideos() {
            try {
                const response = await fetch('/api/videos');
                allVideos = await response.json();
                displayVideos(allVideos);
                document.getElementById('loading').style.display = 'none';
            } catch (error) {
                console.error('Error loading videos:', error);
                document.getElementById('loading').innerHTML = '<p>❌ Error cargando videos</p>';
            }
        }
        
        function displayVideos(videos) {
            const container = document.getElementById('videos-list');
            
            if (videos.length === 0) {
                container.innerHTML = '<div class="no-results"><p>📭 No se encontraron videos</p></div>';
                return;
            }
            
            const html = videos.map(video => {
                video.video_path = video.video_path.replace('.mp4', '_web.mp4');

                const detectionCounts = {};
                video.detections.forEach(detection => {
                    detectionCounts[detection.label] = (detectionCounts[detection.label] || 0) + 1;
                });
                
                const statsHtml = Object.entries(detectionCounts).map(([label, count]) => 
                    `<div class="stat-item">
                        <div class="stat-value">${count}</div>
                        <div class="stat-label">${getLabelIcon(label)} ${label}</div>
                    </div>`
                ).join('');
                
                const detectionsHtml = video.detections.slice(0, 6).map(detection => 
                    `<div class="detection-item">
                        <div class="detection-class">${getLabelIcon(detection.label)} ${detection.label}</div>
                        <div class="detection-confidence">${(detection.confidence * 100).toFixed(1)}%</div>
                        <div class="detection-time">${formatTimestamp(detection.timestamp)}</div>
                    </div>`
                ).join('');
                
                return `
                    <div class="video-card">
                        <div class="video-header" onclick="toggleDetails('${video.video_id}')">
                            <div>
                                <div class="video-title">📹 ${video.filename}</div>
                                <div class="video-date">${formatVideoDate(video.video_timestamp)}</div>
                            </div>
                            <div class="expand-icon">▼</div>
                        </div>
                        <div id="details-${video.video_id}" class="video-details">
                            <div class="stats">
                                ${statsHtml}
                            </div>
                            
                            <h4>🎯 Detecciones recientes:</h4>
                            <div class="detections-grid">
                                ${detectionsHtml}
                            </div>
                            
                            <div class="video-actions">
                                <button class="btn" onclick="playVideo('${video.video_path}', '${video.video_id}')">
                                    ▶️ Reproducir Video
                                </button>
                                <button class="btn btn-secondary" onclick="downloadVideo('${video.video_path}')">
                                    ⬇️ Descargar
                                </button>
                            </div>
                            
                            <div id="player-${video.video_id}"></div>
                        </div>
                    </div>
                `;
            }).join('');
            
            container.innerHTML = html;
        }
        
        function toggleDetails(videoId) {
            const details = document.getElementById(`details-${videoId}`);
            const icon = details.previousElementSibling.querySelector('.expand-icon');
            
            if (details.classList.contains('expanded')) {
                details.classList.remove('expanded');
                icon.style.transform = 'rotate(0deg)';
            } else {
                details.classList.add('expanded');
                icon.style.transform = 'rotate(180deg)';
            }
        }
        
        function playVideo(videoPath, videoId) {
            const playerContainer = document.getElementById(`player-${videoId}`);
            playerContainer.innerHTML = `
                <video class="video-player" controls>
                    <source src="/video/${videoPath}" type="video/mp4">
                    Tu navegador no soporta el elemento video.
                </video>
            `;
        }
        
        function downloadVideo(videoPath) {
            window.open(`/video/${videoPath}`, '_blank');
        }
        
        function filterVideos() {
            const dateFilter = document.getElementById('dateFilter').value;
            const labelFilter = document.getElementById('classFilter').value;
            
            let filtered = allVideos;
            
            if (dateFilter) {
                filtered = filtered.filter(video => 
                    video.video_timestamp.startsWith(dateFilter)
                );
            }
            
            if (labelFilter) {
                filtered = filtered.filter(video => 
                    video.detections.some(detection => detection.label === labelFilter)
                );
            }
            
            displayVideos(filtered);
        }
        
        function formatVideoDate(timestamp) {
            const date = new Date(timestamp);
            return date.toLocaleString('es-ES', {
                year: 'numeric',
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            });
        }
        
        function formatTimestamp(timestamp) {
            const date = new Date(timestamp);
            return date.toLocaleTimeString('es-ES', {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            });
        }
        
        function getLabelIcon(labelName) {
            const icons = {
                'car': '🚗',
                'person': '👤',
                'truck': '🚛',
                'motorcycle': '🏍️',
                'bicycle': '🚲',
                'bus': '🚌',
                'dog': '🐕',
                'cat': '🐱',
                'bear': '🐻',
                'traffic light': '🚦',
                'bird': '🐦',
                'horse': '🐎',
                'sheep': '🐑',
                'cow': '🐄',
                'elephant': '🐘',
                'zebra': '🦓',
                'giraffe': '🦒'
            };
            return icons[labelName] || '📦';
        }
        
        // Cargar videos al inicio
        loadVideos();
    </script>
</body>
</html>
"""

def get_all_videos():
    """Obtiene todos los videos y sus metadatos JSON"""
    videos = []
    
    # Buscar todos los archivos JSON en la estructura de carpetas
    pattern = os.path.join(RECORDINGS_PATH, "**", "*.json")
    json_files = glob.glob(pattern, recursive=True)
    
    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                video_data = json.load(f)
            
            # Extraer el nombre del video
            video_filename = video_data['video']  # e.g., detection_20250701_050808_HgkPAWHSL6.mp4

            # Extraer año, mes y día desde el nombre del archivo
            date_str = video_filename.split('_')[1]  # '20250701'
            time_str = video_filename.split('_')[2]  # '050808'
            
            year = date_str[:4]
            month = date_str[4:6]
            day = date_str[6:8]
            
            hour = time_str[:2]
            minute = time_str[2:4]
            second = time_str[4:6]

            # Construir la ruta relativa completa
            relative_path = os.path.join(year, month, day, video_filename)
            relative_path = relative_path.replace('\\', '/')
            
            video_data['video_path'] = relative_path

            # Verificar si el archivo existe
            video_full_path = os.path.join(RECORDINGS_PATH, relative_path)

            if os.path.exists(video_full_path):
                # Agregar campos adicionales para compatibilidad
                video_data['filename'] = video_filename
                video_data['video_id'] = video_filename.replace('.mp4', '').replace('.', '_')
                
                # Crear timestamp ISO 8601 para el video
                video_data['video_timestamp'] = f"{year}-{month}-{day}T{hour}:{minute}:{second}"
                
                # Convertir detecciones al formato esperado por el frontend
                # (el nuevo formato ya tiene 'label' y 'confidence' como decimal)
                
                videos.append(video_data)
                
        except (json.JSONDecodeError, KeyError, FileNotFoundError, IndexError) as e:
            print(f"Error procesando {json_file}: {e}")
            continue
    
    # Ordenar por timestamp descendente (más recientes primero)
    videos.sort(key=lambda x: x['video_timestamp'], reverse=True)
    return videos

@app.route('/')
def index():
    """Página principal"""
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/videos')
def api_videos():
    """API para obtener lista de videos"""
    try:
        videos = get_all_videos()
        return jsonify(videos)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# http://localhost:5000/video/2025%07%01detection_20250701_055356_5EQH5muk1v.mp4
@app.route('/video/<path:video_path>')
def serve_video(video_path):
    """Servir archivos de video"""
    try:
        full_path = os.path.join(RECORDINGS_PATH, video_path)
        
        if not os.path.exists(full_path):
            return "Video no encontrado", 404
            
        return send_file(full_path, mimetype='video/mp4')
    except Exception as e:
        return f"Error sirviendo video: {str(e)}", 500

@app.route('/api/stats')
def api_stats():
    """API para obtener estadísticas"""
    try:
        videos = get_all_videos()
        
        total_videos = len(videos)
        total_detections = sum(len(video['detections']) for video in videos)
        
        # Contar detecciones por label (antes era 'class')
        label_counts = {}
        for video in videos:
            for detection in video['detections']:
                label = detection['label']
                label_counts[label] = label_counts.get(label, 0) + 1
        
        # Videos por día
        date_counts = {}
        for video in videos:
            date_str = video['video_timestamp'][:10]  # YYYY-MM-DD
            date_counts[date_str] = date_counts.get(date_str, 0) + 1
        
        stats = {
            'total_videos': total_videos,
            'total_detections': total_detections,
            'label_counts': label_counts,  # Cambiado de 'class_counts'
            'date_counts': date_counts
        }
        
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Verificar que la ruta de grabaciones existe
    if not os.path.exists(RECORDINGS_PATH):
        print(f"⚠️  Advertencia: La ruta {RECORDINGS_PATH} no existe")
        print("📁 Creando estructura de carpetas...")
        os.makedirs(RECORDINGS_PATH, exist_ok=True)
    
    print("🚀 Iniciando servidor Flask...")
    print(f"📂 Ruta de grabaciones: {RECORDINGS_PATH}")
    print("🌐 Servidor disponible en: http://localhost:5000")
    
    app.run(host='0.0.0.0', port=5000, debug=True)