import os
import json
import glob
from pathlib import Path
import subprocess
from flask import Flask, render_template_string, jsonify, request, send_file, session, redirect, url_for, flash
import hashlib
import secrets
from datetime import datetime, date, timedelta
from functools import wraps

app = Flask(__name__)

# Configuración de rutas
BASE_PATH = "/home/sedax/rtsp_ia"
RECORDINGS_PATH = os.path.join(BASE_PATH, "recordings")

# Configuración de seguridad
app.secret_key = secrets.token_hex(32)  # Genera una clave secreta aleatoria
app.permanent_session_lifetime = timedelta(hours=8)  # Sesión expira en 8 horas

# Credenciales hardcodeadas (hasheadas)
USERS = {
    'admin': hashlib.sha256('password123'.encode()).hexdigest(),  # Cambiar esta contraseña
}

def hash_password(password):
    """Hashea una contraseña con SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(username, password):
    """Verifica las credenciales del usuario"""
    if username in USERS:
        return USERS[username] == hash_password(password)
    return False

def login_required(f):
    """Decorador para proteger rutas"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or not session['logged_in']:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def rate_limit_check():
    """Protección básica contra ataques de fuerza bruta"""
    client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
    current_time = datetime.now()
    
    if 'login_attempts' not in session:
        session['login_attempts'] = {}
    
    # ✅ Usar bucle clásico para limpiar intentos antiguos
    cleaned_attempts = {}
    for ip, attempts in session['login_attempts'].items():
        try:
            last_attempt = datetime.fromisoformat(attempts['last_attempt'])
            if current_time - last_attempt < timedelta(minutes=15):
                cleaned_attempts[ip] = attempts
        except Exception:
            continue  # en caso de formato inválido o dato corrupto

    session['login_attempts'] = cleaned_attempts

    # ✅ Verificar si el cliente está bloqueado
    if client_ip in session['login_attempts']:
        attempts = session['login_attempts'][client_ip]
        try:
            last_attempt = datetime.fromisoformat(attempts['last_attempt'])
            if attempts['count'] >= 5 and current_time - last_attempt < timedelta(minutes=15):
                return False
        except Exception:
            pass  # si el dato está corrupto, no bloquear

    return True

def record_failed_attempt():
    """Registra un intento fallido de login"""
    client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
    current_time = datetime.now().isoformat()
    
    if 'login_attempts' not in session:
        session['login_attempts'] = {}
    
    if client_ip not in session['login_attempts']:
        session['login_attempts'][client_ip] = {'count': 0, 'last_attempt': current_time}
    
    session['login_attempts'][client_ip]['count'] += 1
    session['login_attempts'][client_ip]['last_attempt'] = current_time

# ================================
# PLANTILLA HTML PARA LOGIN
# ================================
LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Video Surveillance - Login</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif;
            background: #f0f0f0;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        .login-container {
            background: white;
            padding: 40px;
            border-radius: 12px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
            width: 100%;
            max-width: 400px;
        }
        
        .login-header {
            text-align: center;
            margin-bottom: 30px;
        }
        
        .logo {
            font-size: 48px;
            margin-bottom: 16px;
        }
        
        .login-title {
            font-size: 24px;
            font-weight: 600;
            color: #1a1a1a;
            margin-bottom: 8px;
        }
        
        .login-subtitle {
            color: #666;
            font-size: 14px;
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        .form-label {
            display: block;
            margin-bottom: 6px;
            font-weight: 500;
            color: #333;
            font-size: 14px;
        }
        
        .form-input {
            width: 100%;
            padding: 12px 16px;
            border: 1px solid #ddd;
            border-radius: 6px;
            font-size: 16px;
            transition: border-color 0.2s;
        }
        
        .form-input:focus {
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }
        
        .login-btn {
            width: 100%;
            background: #667eea;
            color: white;
            border: none;
            padding: 12px 16px;
            border-radius: 6px;
            font-size: 16px;
            font-weight: 500;
            cursor: pointer;
            transition: background-color 0.2s;
        }
        
        .login-btn:hover {
            background: #5a67d8;
        }
        
        .login-btn:disabled {
            background: #ccc;
            cursor: not-allowed;
        }
        
        .alert {
            padding: 12px;
            border-radius: 6px;
            margin-bottom: 20px;
            font-size: 14px;
        }
        
        .alert-error {
            background: #fee;
            color: #c53030;
            border: 1px solid #fecaca;
        }
        
        .alert-warning {
            background: #fffbeb;
            color: #d69e2e;
            border: 1px solid #fbd38d;
        }
        
        .security-info {
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #eee;
            text-align: center;
        }
        
        .security-info p {
            font-size: 12px;
            color: #666;
            line-height: 1.4;
        }
        
        .attempts-remaining {
            font-size: 12px;
            color: #d69e2e;
            margin-top: 8px;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="login-header">
            <div class="logo">🔐</div>
            <h1 class="login-title">Video Surveillance</h1>
            <p class="login-subtitle">Acceso al sistema de vigilancia</p>
        </div>
        
        {% if error %}
        <div class="alert alert-error">
            {{ error }}
        </div>
        {% endif %}
        
        {% if warning %}
        <div class="alert alert-warning">
            {{ warning }}
        </div>
        {% endif %}
        
        <form method="POST">
            <div class="form-group">
                <label for="username" class="form-label">Usuario</label>
                <input type="text" id="username" name="username" class="form-input" 
                       required autocomplete="username" value="{{ username or '' }}">
            </div>
            
            <div class="form-group">
                <label for="password" class="form-label">Contraseña</label>
                <input type="password" id="password" name="password" class="form-input" 
                       required autocomplete="current-password">
            </div>
            
            <button type="submit" class="login-btn" {{ 'disabled' if blocked else '' }}>
                {% if blocked %}
                Bloqueado temporalmente
                {% else %}
                Iniciar Sesión
                {% endif %}
            </button>
            
            {% if attempts_count %}
            <div class="attempts-remaining">
                Intentos restantes: {{ 5 - attempts_count }}
            </div>
            {% endif %}
        </form>
        
        <div class="security-info">
            <p>🔒 Conexión segura protegida con autenticación</p>
            <p>Máximo 5 intentos cada 15 minutos</p>
        </div>
    </div>
</body>
</html>
"""

# Template HTML con diseño minimalista
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Video Surveillance</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif;
            background: #ffffff;
            color: #1a1a1a;
            line-height: 1.6;
            font-size: 14px;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 0 20px;
        }
        
        .header {
            border-bottom: 1px solid #e5e5e5;
            padding: 30px 0;
            background: #ffffff;
            position: sticky;
            top: 0;
            z-index: 100;
            backdrop-filter: blur(10px);
            background: rgba(255, 255, 255, 0.95);
        }
        
        .header-content {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .logo {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        
        .logo h1 {
            font-size: 24px;
            font-weight: 600;
            letter-spacing: -0.02em;
        }
        
        .logo-icon {
            width: 32px;
            height: 32px;
            background: #1a1a1a;
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-size: 16px;
        }
        
        .stats-summary {
            display: flex;
            gap: 24px;
            font-size: 12px;
            color: #666;
        }
        
        .stat-item {
            text-align: center;
        }
        
        .stat-number {
            font-size: 20px;
            font-weight: 600;
            color: #1a1a1a;
            display: block;
        }
        
        .filters {
            background: #fafafa;
            border: 1px solid #e5e5e5;
            border-radius: 8px;
            margin: 24px 0;
            padding: 24px;
        }
        
        .filters-grid {
            display: grid;
            grid-template-columns: 1fr 1fr auto;
            gap: 16px;
            align-items: end;
        }
        
        .filter-group {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        
        .filter-label {
            font-size: 12px;
            font-weight: 500;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .filter-input {
            padding: 12px 16px;
            border: 1px solid #d1d1d1;
            border-radius: 6px;
            font-size: 14px;
            background: #ffffff;
            transition: all 0.2s ease;
        }
        
        .filter-input:focus {
            outline: none;
            border-color: #1a1a1a;
            box-shadow: 0 0 0 3px rgba(26, 26, 26, 0.1);
        }
        
        .btn {
            background: #1a1a1a;
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s ease;
            height: fit-content;
        }
        
        .btn:hover {
            background: #333;
            transform: translateY(-1px);
        }
        
        .btn:active {
            transform: translateY(0);
        }
        
        .btn-secondary {
            background: transparent;
            color: #1a1a1a;
            border: 1px solid #d1d1d1;
        }
        
        .btn-secondary:hover {
            background: #f5f5f5;
            border-color: #1a1a1a;
        }
        
        .videos-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
            gap: 24px;
            margin: 24px 0;
        }
        
        .video-card {
            background: #ffffff;
            border: 1px solid #e5e5e5;
            border-radius: 12px;
            overflow: hidden;
            transition: all 0.3s ease;
            position: relative;
        }
        
        .video-card:hover {
            transform: translateY(-4px);
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
            border-color: #d1d1d1;
        }
        
        .video-thumbnail {
            aspect-ratio: 16/9;
            background: #f5f5f5;
            position: relative;
            overflow: hidden;
            cursor: pointer;
        }
        
        .video-preview {
            width: 100%;
            height: 100%;
            object-fit: cover;
            border-radius: 0;
        }
        
        .video-placeholder {
            width: 100%;
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            background: linear-gradient(135deg, #f5f5f5 0%, #e5e5e5 100%);
            color: #999;
            font-size: 48px;
        }
        
        .video-duration {
            position: absolute;
            bottom: 8px;
            right: 8px;
            background: rgba(0, 0, 0, 0.8);
            color: white;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 500;
        }
        
        .play-button {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            width: 60px;
            height: 60px;
            background: rgba(0, 0, 0, 0.8);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-size: 20px;
            cursor: pointer;
            transition: all 0.2s ease;
            z-index: 2;
        }
        
        .video-thumbnail:hover .play-button {
            background: rgba(0, 0, 0, 0.9);
            transform: translate(-50%, -50%) scale(1.1);
        }
        
        .video-controls {
            position: absolute;
            bottom: 0;
            left: 0;
            right: 0;
            background: linear-gradient(transparent, rgba(0, 0, 0, 0.8));
            padding: 20px 16px 16px 16px;
            opacity: 0;
            transition: opacity 0.2s ease;
        }
        
        .video-thumbnail:hover .video-controls {
            opacity: 1;
        }
        
        .video-info {
            padding: 20px;
        }
        
        .video-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 12px;
        }
        
        .video-title {
            font-size: 16px;
            font-weight: 600;
            color: #1a1a1a;
            line-height: 1.3;
        }
        
        .video-date {
            font-size: 12px;
            color: #666;
            text-align: right;
            line-height: 1.3;
        }
        
        .detection-summary {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin: 16px 0;
        }
        
        .detection-tag {
            background: #f5f5f5;
            border: 1px solid #e5e5e5;
            padding: 4px 8px;
            border-radius: 16px;
            font-size: 11px;
            font-weight: 500;
            color: #666;
            display: flex;
            align-items: center;
            gap: 4px;
        }
        
        .detection-count {
            background: #1a1a1a;
            color: white;
            border-radius: 10px;
            padding: 2px 6px;
            font-size: 10px;
            min-width: 16px;
            text-align: center;
        }
        
        .video-details {
            border-top: 1px solid #f0f0f0;
            padding-top: 16px;
            margin-top: 16px;
        }
        
        .details-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
            margin-bottom: 16px;
        }
        
        .detail-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 12px;
        }
        
        .detail-label {
            color: #666;
            font-weight: 500;
        }
        
        .detail-value {
            color: #1a1a1a;
            font-weight: 600;
        }
        
        .video-actions {
            display: flex;
            gap: 8px;
            margin-top: 16px;
        }
        
        .btn-small {
            padding: 8px 16px;
            font-size: 12px;
            flex: 1;
        }
        
        .hidden {
            display: none;
        }
        
        .video-player-container {
            margin-top: 16px;
            border-radius: 8px;
            overflow: hidden;
            background: #f5f5f5;
        }
        
        .video-player {
            width: 100%;
            max-width: 100%;
            display: block;
        }
        
        .recent-detections {
            margin-top: 16px;
        }
        
        .recent-detections h4 {
            font-size: 12px;
            font-weight: 600;
            color: #1a1a1a;
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .detections-list {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        
        .detection-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 12px;
            background: #fafafa;
            border-radius: 6px;
            font-size: 11px;
        }
        
        .detection-info {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .detection-class {
            font-weight: 600;
            color: #1a1a1a;
        }
        
        .detection-confidence {
            color: #666;
        }
        
        .detection-time {
            color: #999;
            font-size: 10px;
        }
        
        .loading {
            text-align: center;
            padding: 60px 20px;
            color: #666;
        }
        
        .no-results {
            text-align: center;
            padding: 60px 20px;
            color: #666;
        }
        
        .no-results-icon {
            font-size: 48px;
            margin-bottom: 16px;
            opacity: 0.5;
        }
        
        .spinner {
            width: 32px;
            height: 32px;
            border: 2px solid #f3f3f3;
            border-top: 2px solid #1a1a1a;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin: 0 auto 16px auto;
        }
        
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        @media (max-width: 768px) {
            .container {
                padding: 0 16px;
            }
            
            .header-content {
                flex-direction: column;
                gap: 16px;
                text-align: center;
            }
            
            .stats-summary {
                justify-content: center;
            }
            
            .filters-grid {
                grid-template-columns: 1fr;
                gap: 16px;
            }
            
            .videos-grid {
                grid-template-columns: 1fr;
                gap: 16px;
            }
            
            .video-header {
                flex-direction: column;
                align-items: flex-start;
                gap: 8px;
            }
            
            .details-grid {
                grid-template-columns: 1fr;
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
        <div class="container">
            <div class="header-content">
                <div class="logo">
                    <div class="logo-icon">📹</div>
                    <h1>Video Surveillance</h1>
                </div>

                <div class="user-info">
                    <span style="margin-right: 16px; color: #666; font-size: 12px;">
                        👤 {{ session.username }}
                    </span>
                    <a href="/logout" style="color: #666; text-decoration: none; font-size: 12px; 
                       padding: 6px 12px; border: 1px solid #ddd; border-radius: 4px; 
                       transition: all 0.2s;">
                        Cerrar Sesión
                    </a>
                </div>

                <div class="stats-summary">
                    <div class="stat-item">
                        <span class="stat-number" id="total-videos">0</span>
                        <span>Videos</span>
                    </div>
                    <div class="stat-item">
                        <span class="stat-number" id="total-detections">0</span>
                        <span>Detecciones</span>
                    </div>
                    <!--
                    <div class="stat-item">
                        <span class="stat-number" id="active-cameras">1</span>
                        <span>Cámaras</span>
                    </div>
                    -->
                </div>
            </div>
        </div>
    </div>
    
    <div class="container">
        <div class="filters">
            <div class="filters-grid">
                <div class="filter-group">
                    <label class="filter-label" for="dateFilter">Fecha</label>
                    <input type="date" id="dateFilter" class="filter-input">
                </div>
                <div class="filter-group">
                    <label class="filter-label" for="classFilter">Detección</label>
                    <select id="classFilter" class="filter-input">
                        <option value="">Todas</option>
                        <option value="person">Persona</option>
                        <option value="car">Automóvil</option>
                        <option value="truck">Camión</option>
                        <option value="motorcycle">Motocicleta</option>
                        <option value="bicycle">Bicicleta</option>
                        <option value="dog">Perro</option>
                        <option value="cat">Gato</option>
                        <option value="bird">Pájaro</option>
                        <option value="bear">Oso</option>
                        <option value="traffic light">Semáforo</option>
                    </select>
                </div>
                <button class="btn" onclick="filterVideos()">Filtrar</button>
            </div>
        </div>
        
        <div id="loading" class="loading">
            <div class="spinner"></div>
            <p>Cargando videos...</p>
        </div>
        
        <div id="videos-grid" class="videos-grid"></div>
        
        <div id="no-results" class="no-results" style="display: none;">
            <div class="no-results-icon">🔍</div>
            <p>No se encontraron videos con los filtros seleccionados</p>
        </div>
    </div>

    <script>
        let allVideos = [];
        
        async function loadVideos() {
            try {
                const response = await fetch('/api/videos');
                allVideos = await response.json();
                displayVideos(allVideos);
                updateStats();
                document.getElementById('loading').style.display = 'none';
            } catch (error) {
                console.error('Error loading videos:', error);
                document.getElementById('loading').innerHTML = '<p>Error cargando videos</p>';
            }
        }
        
        function updateStats() {
            const totalVideos = allVideos.length;
            const totalDetections = allVideos.reduce((sum, video) => sum + video.detections.length, 0);
            
            document.getElementById('total-videos').textContent = totalVideos;
            document.getElementById('total-detections').textContent = totalDetections;
        }
        
        function displayVideos(videos) {
            const container = document.getElementById('videos-grid');
            const noResults = document.getElementById('no-results');
            
            if (videos.length === 0) {
                container.style.display = 'none';
                noResults.style.display = 'block';
                return;
            }
            
            container.style.display = 'grid';
            noResults.style.display = 'none';
            
            const html = videos.map(video => {
                if (!video.video_path.endsWith('_web.mp4')) {
                    video.video_path = video.video_path.replace('.mp4', '_web.mp4');
                }

                const detectionCounts = {};
                video.detections.forEach(detection => {
                    detectionCounts[detection.label] = (detectionCounts[detection.label] || 0) + 1;
                });
                
                const detectionTags = Object.entries(detectionCounts).slice(0, 5).map(([label, count]) => 
                    `<div class="detection-tag">
                        ${getLabelIcon(label)} ${label}
                        <span class="detection-count">${count}</span>
                    </div>`
                ).join('');
                
                const recentDetections = video.detections.slice(0, 3).map(detection => 
                    `<div class="detection-item">
                        <div class="detection-info">
                            <span class="detection-class">${getLabelIcon(detection.label)} ${detection.label}</span>
                            <span class="detection-confidence">${(detection.confidence * 100).toFixed(0)}%</span>
                        </div>
                        <div class="detection-time">${formatTime(detection.timestamp)}</div>
                    </div>`
                ).join('');
                
                const fileSize = video.file_size || '2.1 MB';
                const duration = video.duration || '30s';
                const resolution = video.resolution || 'SD';
                
                return `
                    <div class="video-card">
                        <div class="video-thumbnail" onclick="toggleVideoPlayback('${video.video_path}', '${video.video_id}')">
                            <div id="placeholder-${video.video_id}" class="video-placeholder">📹</div>
                            <video id="video-${video.video_id}" class="video-preview hidden" controls>
                                <source src="/video/${video.video_path}" type="video/mp4">
                            </video>
                            <div id="play-btn-${video.video_id}" class="play-button">▶</div>
                            <div class="video-controls">
                                <div class="video-duration">${duration}</div>
                            </div>
                        </div>
                        
                        <div class="video-info">
                            <div class="video-header">
                                <div class="video-title">${video.filename.replace('.mp4', '')}</div>
                                <div class="video-date">
                                    ${formatDate(video.video_timestamp)}<br>
                                    ${formatTime(video.video_timestamp)}
                                </div>
                            </div>
                            
                            <div class="detection-summary">
                                ${detectionTags}
                            </div>
                            
                            <div class="video-details">
                                <div class="details-grid">
                                    <div class="detail-item">
                                        <span class="detail-label">Detecciones</span>
                                        <span class="detail-value">${video.detections.length}</span>
                                    </div>
                                    <div class="detail-item">
                                        <span class="detail-label">Tamaño</span>
                                        <span class="detail-value">${fileSize}</span>
                                    </div>
                                    <div class="detail-item">
                                        <span class="detail-label">Duración</span>
                                        <span class="detail-value">${duration}</span>
                                    </div>
                                    <div class="detail-item">
                                        <span class="detail-label">Resolución</span>
                                        <span class="detail-value">${resolution}</span>
                                    </div>
                                </div>
                                
                                <div class="recent-detections">
                                    <h4>Detecciones Recientes</h4>
                                    <div class="detections-list">
                                        ${recentDetections}
                                    </div>
                                </div>
                                
                                <div class="video-actions">
                                    <button class="btn-secondary btn-small" onclick="downloadVideo('${video.video_path}')">
                                        Descargar
                                    </button>
                                </div>
                                
                            </div>
                        </div>
                    </div>
                `;
            }).join('');
            
            container.innerHTML = html;
        }
        
        function toggleVideoPlayback(videoPath, videoId) {
            const placeholder = document.getElementById(`placeholder-${videoId}`);
            const video = document.getElementById(`video-${videoId}`);
            const playBtn = document.getElementById(`play-btn-${videoId}`);
            
            if (video.classList.contains('hidden')) {
                // Mostrar video y ocultar placeholder
                placeholder.classList.add('hidden');
                video.classList.remove('hidden');
                playBtn.classList.add('hidden');
                video.play();
            } else {
                // Ocultar video y mostrar placeholder
                video.pause();
                video.classList.add('hidden');
                placeholder.classList.remove('hidden');
                playBtn.classList.remove('hidden');
            }
        }
        
        function playVideo(videoPath, videoId) {
            toggleVideoPlayback(videoPath, videoId);
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
        
        function formatDate(timestamp) {
            const date = new Date(timestamp);
            return date.toLocaleDateString('es-ES', {
                day: 'numeric',
                month: 'short',
                year: 'numeric'
            });
        }
        
        function formatTime(timestamp) {
            const date = new Date(timestamp);
            return date.toLocaleTimeString('es-ES', {
                hour: '2-digit',
                minute: '2-digit'
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
                'cow': '🐄'
            };
            return icons[labelName] || '📦';
        }
        
        // Cargar videos al inicio
        loadVideos();
        
        // Auto-refresh cada 30 segundos
        setInterval(loadVideos, 30000);
    </script>
</body>
</html>
"""

def get_video_info(video_path):
    """Obtiene información del video usando ffprobe"""

    if not video_path.endswith('_web.mp4'):
        video_path = video_path.replace('.mp4', '_web.mp4')

    try:
        # Comando ffprobe para obtener información del video
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams',
            video_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            info = json.loads(result.stdout)
            
            # Obtener duración
            duration = float(info['format']['duration'])
            duration_str = format_duration(duration)
            
            # Obtener tamaño del archivo
            size = int(info['format']['size'])
            size_str = format_file_size(size)
            
            # Obtener resolución del video
            video_stream = next((s for s in info['streams'] if s['codec_type'] == 'video'), None)
            resolution = 'SD'
            if video_stream:
                width = int(video_stream.get('width', 0))
                height = int(video_stream.get('height', 0))
                if height >= 1080:
                    resolution = 'FHD'
                elif height >= 720:
                    resolution = 'HD'
                elif height >= 480:
                    resolution = 'SD'
            
            return {
                'duration': duration_str,
                'size': size_str,
                'resolution': resolution
            }
    except Exception as e:
        print(f"Error obteniendo info del video {video_path}: {e}")
    
    # Valores por defecto si no se puede obtener la información
    try:
        # Al menos obtener el tamaño del archivo
        size = os.path.getsize(video_path)
        size_str = format_file_size(size)
        return {
            'duration': '00:30',
            'size': size_str,
            'resolution': 'SD'
        }
    except:
        return {
            'duration': '00:30',
            'size': '2.1 MB',
            'resolution': 'SD'
        }

def format_duration(seconds):
    """Convierte segundos a formato MM:SS o HH:MM:SS"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int(seconds % 60)
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    else:
        return f"{minutes:02d}:{seconds:02d}"

def format_file_size(bytes):
    """Convierte bytes a formato legible"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes < 1024.0:
            return f"{bytes:.1f} {unit}"
        bytes /= 1024.0
    return f"{bytes:.1f} TB"
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
                # Obtener información real del video
                video_info = get_video_info(video_full_path)
                
                # Agregar campos adicionales para compatibilidad
                video_data['filename'] = video_filename
                video_data['video_id'] = video_filename.replace('.mp4', '').replace('.', '_')
                
                # Crear timestamp ISO 8601 para el video
                video_data['video_timestamp'] = f"{year}-{month}-{day}T{hour}:{minute}:{second}"
                
                # Agregar información del video
                video_data['duration'] = video_info['duration']
                video_data['file_size'] = video_info['size']
                video_data['resolution'] = video_info['resolution']
                
                videos.append(video_data)
                
        except (json.JSONDecodeError, KeyError, FileNotFoundError, IndexError) as e:
            print(f"Error procesando {json_file}: {e}")
            continue
    
    # Ordenar por timestamp descendente (más recientes primero)
    videos.sort(key=lambda x: x['video_timestamp'], reverse=True)
    return videos

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Página y procesamiento de login"""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        # Verificar rate limiting
        if not rate_limit_check():
            return render_template_string(LOGIN_TEMPLATE, 
                                        error="Demasiados intentos fallidos. Espera 15 minutos.",
                                        blocked=True)
        
        # Verificar credenciales
        if username and password and verify_password(username, password):
            session['logged_in'] = True
            session['username'] = username
            session['login_time'] = datetime.now().isoformat()
            session.permanent = True
            
            # Limpiar intentos fallidos
            if 'login_attempts' in session:
                client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
                session['login_attempts'].pop(client_ip, None)
            
            return redirect(url_for('index'))
        else:
            record_failed_attempt()
            
            # Calcular intentos restantes
            client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
            attempts_count = 0
            if 'login_attempts' in session and client_ip in session['login_attempts']:
                attempts_count = session['login_attempts'][client_ip]['count']
            
            error_msg = "Usuario o contraseña incorrectos"
            if attempts_count >= 4:
                error_msg += " - Un intento más y serás bloqueado"
            
            return render_template_string(LOGIN_TEMPLATE, 
                                        error=error_msg,
                                        username=username,
                                        attempts_count=attempts_count)
    
    # GET request - mostrar formulario
    # Verificar si ya está logueado
    if 'logged_in' in session and session['logged_in']:
        return redirect(url_for('index'))
    
    # Verificar si está bloqueado
    blocked = not rate_limit_check()
    warning = None
    
    client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
    attempts_count = 0
    if 'login_attempts' in session and client_ip in session['login_attempts']:
        attempts_count = session['login_attempts'][client_ip]['count']
        if attempts_count > 0:
            warning = f"Has realizado {attempts_count} intentos fallidos"
    
    return render_template_string(LOGIN_TEMPLATE, 
                                blocked=blocked, 
                                warning=warning,
                                attempts_count=attempts_count)

@app.route('/logout')
def logout():
    """Cerrar sesión"""
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    """Página principal - PROTEGIDA"""
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/videos')
@login_required
def api_videos():
    """API para obtener lista de videos - PROTEGIDA"""
    try:
        videos = get_all_videos()
        return jsonify(videos)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/video/<path:video_path>')
@login_required
def serve_video(video_path):
    """Servir archivos de video - PROTEGIDO"""
    try:
        full_path = os.path.join(RECORDINGS_PATH, video_path)
        
        if not os.path.exists(full_path):
            return "Video no encontrado", 404
            
        return send_file(full_path, mimetype='video/mp4')
    except Exception as e:
        return f"Error sirviendo video: {str(e)}", 500

@app.route('/api/stats')
@login_required
def api_stats():
    """API para obtener estadísticas - PROTEGIDA"""
    try:
        videos = get_all_videos()
        
        total_videos = len(videos)
        total_detections = sum(len(video['detections']) for video in videos)
        
        # Contar detecciones por label
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
            'label_counts': label_counts,
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