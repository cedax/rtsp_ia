import subprocess
import cv2
import numpy as np
import threading
import queue
import time
import datetime
import json
import os
import secrets
import string
import math
import tkinter as tk
from ultralytics import YOLO
from collections import deque
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

class WindowManager:
    def __init__(self):
        self.screen_width, self.screen_height = self.get_screen_size()
        self.window_positions = {}
        self.window_size = None
        
    def get_screen_size(self):
        """Obtener el tamaño de la pantalla"""
        try:
            root = tk.Tk()
            screen_width = root.winfo_screenwidth()
            screen_height = root.winfo_screenheight()
            root.destroy()
            return screen_width, screen_height
        except:
            # Fallback si no funciona tkinter
            return 1920, 1080
    
    def calculate_layout(self, num_cameras):
        """Calcular la distribución óptima de ventanas"""
        if num_cameras == 1:
            cols, rows = 1, 1
        elif num_cameras == 2:
            cols, rows = 2, 1
        elif num_cameras <= 4:
            cols, rows = 2, 2
        elif num_cameras <= 6:
            cols, rows = 3, 2
        elif num_cameras <= 9:
            cols, rows = 3, 3
        else:
            # Para más de 9 cámaras, usar una grilla cuadrada
            cols = rows = math.ceil(math.sqrt(num_cameras))
        
        # Calcular tamaño de cada ventana (dejando espacio para bordes)
        margin = 10
        window_width = (self.screen_width - margin * (cols + 1)) // cols
        window_height = (self.screen_height - margin * (rows + 1)) // rows
        
        # Mantener aspect ratio 16:9
        target_ratio = 16/9
        if window_width / window_height > target_ratio:
            window_width = int(window_height * target_ratio)
        else:
            window_height = int(window_width / target_ratio)
        
        self.window_size = (window_width, window_height)
        
        # Calcular posiciones
        positions = []
        for i in range(num_cameras):
            row = i // cols
            col = i % cols
            x = margin + col * (window_width + margin)
            y = margin + row * (window_height + margin)
            positions.append((x, y))
        
        return positions
    
    def set_window_position(self, window_name, position):
        """Establecer la posición de una ventana"""
        x, y = position
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, self.window_size[0], self.window_size[1])
        cv2.moveWindow(window_name, x, y)
        self.window_positions[window_name] = position

class CameraProcessor:
    def __init__(self, camera_name, rtsp_url, camera_id, window_manager, window_position):
        self.camera_name = camera_name
        self.rtsp_url = rtsp_url
        self.camera_id = camera_id
        self.window_manager = window_manager
        self.window_position = window_position
        self.width, self.height = 1280, 720
        self.fps = 20
        
        # Variables específicas de la cámara
        self.frame_queue = queue.Queue(maxsize=1)
        self.stop_event = threading.Event()
        self.detections = []
        self.recording = False
        self.last_detection_time = 0
        self.video_writer = None
        self.detection_log = []
        self.current_video_path = None
        self.conf_threshold = 0.55
        
        # Buffer circular para pregrabación
        self.prebuffer_seconds = 5
        self.prebuffer = deque(maxlen=self.prebuffer_seconds * self.fps)
        self.seconds_to_stop_before_last_detection = 5
        
        # Comando FFmpeg
        self.command = [
            "ffmpeg", "-rtsp_transport", "tcp", "-i", self.rtsp_url, 
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-"
        ]
        
        # Proceso FFmpeg
        self.process = None
        
        print(f"[{self.camera_name}] Configurada - URL: {self.rtsp_url}")

    def generate_uid(self, length=10):
        return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(length))

    def yolo_worker(self, model):
        """Worker para procesamiento YOLO específico de esta cámara"""
        while not self.stop_event.is_set():
            try:
                frame = self.frame_queue.get(timeout=1)
                results = model(frame, conf=self.conf_threshold, iou=0.45, verbose=False)[0]
                self.detections = [
                    (int(b.xyxy[0][0]), int(b.xyxy[0][1]), int(b.xyxy[0][2]), int(b.xyxy[0][3]),
                     model.names[int(b.cls[0])], float(b.conf[0])) for b in results.boxes
                ]
                if any(conf >= self.conf_threshold for *_, conf in self.detections):
                    self.last_detection_time = time.time()
                    for *_, label, conf in self.detections:
                        if conf >= self.conf_threshold:
                            print(f"[{self.camera_name}] Detectado: {label} con {conf*100:.2f}% de confianza")
                self.frame_queue.task_done()
            except (queue.Empty, Exception) as e:
                if not isinstance(e, queue.Empty):
                    print(f"[{self.camera_name}] YOLO ERROR: {e}")

    def start_recording(self):
        """Iniciar grabación para esta cámara"""
        self.detection_log = []
        now = datetime.datetime.now()
        folder_path = os.path.join("recordings", self.camera_name, str(now.year), f"{now.month:02d}", f"{now.day:02d}")
        os.makedirs(folder_path, exist_ok=True)

        filename = f"detection_{now.strftime('%Y%m%d_%H%M%S')}_{self.generate_uid()}.mp4"
        self.current_video_path = os.path.join(folder_path, filename)

        # Configuración optimizada para streaming web
        fourcc = cv2.VideoWriter_fourcc(*'avc1')  # H.264 codec
        self.video_writer = cv2.VideoWriter(self.current_video_path, fourcc, self.fps, (self.width, self.height))
        
        # Verificar si el writer se inicializó correctamente
        if not self.video_writer.isOpened():
            print(f"[{self.camera_name}] Error: No se pudo inicializar VideoWriter con avc1, probando con mp4v...")
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            self.video_writer = cv2.VideoWriter(self.current_video_path, fourcc, self.fps, (self.width, self.height))
        
        print(f"[{self.camera_name}] Iniciando grabación: {self.current_video_path}")

        # Escribir frames del prebuffer
        for buffered_frame in self.prebuffer:
            self.video_writer.write(buffered_frame)

    def stop_recording(self):
        """Detener grabación para esta cámara"""
        if self.video_writer:
            self.video_writer.release()
            self.video_writer = None
            print(f"[{self.camera_name}] Grabación detenida")

            if self.current_video_path:
                # Post-procesamiento con FFmpeg para optimizar streaming
                self.optimize_for_web(self.current_video_path)
                
                json_path = os.path.splitext(self.current_video_path)[0] + ".json"
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "camera": self.camera_name,
                        "video": os.path.basename(self.current_video_path), 
                        "detections": self.detection_log
                    }, f, indent=2)
                print(f"[{self.camera_name}] Detecciones guardadas en: {json_path}")

    def optimize_for_web(self, video_path):
        """Post-procesa el video para optimizarlo para streaming web"""
        try:
            base_name = os.path.splitext(video_path)[0]
            optimized_path = f"{base_name}_web.mp4"
            
            ffmpeg_cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-profile:v", "main",
                "-level", "3.1",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-maxrate", "2M",
                "-bufsize", "4M",
                "-g", str(self.fps * 2),
                "-sc_threshold", "0",
                optimized_path
            ]
            
            print(f"[{self.camera_name}] Optimizando video para web: {optimized_path}")
            result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"[{self.camera_name}] Video optimizado guardado: {optimized_path}")
            else:
                print(f"[{self.camera_name}] Error al optimizar video: {result.stderr}")
                
        except Exception as e:
            print(f"[{self.camera_name}] Error en optimización: {e}")

    def process_camera(self, model):
        """Proceso principal para esta cámara"""
        # Iniciar worker YOLO
        yolo_thread = threading.Thread(target=self.yolo_worker, args=(model,), daemon=True)
        yolo_thread.start()
        
        # Configurar ventana
        window_name = f"Camara - {self.camera_name}"
        self.window_manager.set_window_position(window_name, self.window_position)
        
        # Iniciar proceso FFmpeg
        try:
            self.process = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            print(f"[{self.camera_name}] Proceso FFmpeg iniciado")
        except Exception as e:
            print(f"[{self.camera_name}] Error al iniciar FFmpeg: {e}")
            return

        try:
            while not self.stop_event.is_set():
                raw = self.process.stdout.read(self.width * self.height * 3)
                if len(raw) != self.width * self.height * 3:
                    print(f"[{self.camera_name}] Error leyendo frame, reconectando...")
                    break

                frame = np.frombuffer(raw, np.uint8).reshape((self.height, self.width, 3))

                # Enviar a detección
                if not self.frame_queue.full():
                    self.frame_queue.put_nowait(frame.copy())

                # Dibujar detecciones
                annotated = frame.copy()
                for x1, y1, x2, y2, label, conf in self.detections:
                    if conf >= self.conf_threshold:
                        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
                        cv2.putText(annotated, f"{label} {conf*100:.1f}%", (x1, y1-10),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

                # Agregar al prebuffer
                self.prebuffer.append(annotated.copy())

                # Guardar detecciones si está grabando
                if self.recording and self.detections:
                    timestamp = datetime.datetime.now().isoformat()
                    self.detection_log.extend([
                        {
                            "timestamp": timestamp,
                            "label": label,
                            "confidence": round(conf, 3),
                            "box": [x1, y1, x2, y2]
                        }
                        for x1, y1, x2, y2, label, conf in self.detections if conf >= self.conf_threshold
                    ])

                # Control de grabación
                current_time = time.time()
                if not self.recording and (current_time - self.last_detection_time) < 1:
                    self.recording = True
                    self.start_recording()
                elif self.recording and (current_time - self.last_detection_time) >= self.seconds_to_stop_before_last_detection:
                    self.recording = False
                    self.stop_recording()

                # Grabar frame
                if self.recording and self.video_writer:
                    self.video_writer.write(annotated)

                # Mostrar estado
                if self.recording:
                    cv2.putText(annotated, "GRABANDO", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                
                # Mostrar nombre de cámara
                cv2.putText(annotated, self.camera_name, (10, self.height - 20), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

                cv2.imshow(window_name, annotated)

                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

        except Exception as e:
            print(f"[{self.camera_name}] Error en procesamiento: {e}")
        finally:
            if self.recording:
                self.stop_recording()
            self.stop_event.set()
            if self.process:
                self.process.terminate()
            cv2.destroyWindow(window_name)
            print(f"[{self.camera_name}] Sistema detenido")

def load_cameras_from_env():
    """Cargar configuración de cámaras desde .env"""
    cameras = {}
    camera_id = 1
    
    # Buscar todas las variables que empiecen con CAMERA_
    for key, value in os.environ.items():
        if key.startswith('CAMERA_') and value:
            camera_name = key
            rtsp_url = value
            cameras[camera_name] = {
                'url': rtsp_url,
                'id': camera_id
            }
            camera_id += 1
            
    return cameras

def main():
    """Función principal para manejar múltiples cámaras"""
    print("Iniciando sistema multi-cámara YOLO con auto-organización...")
    
    # Cargar modelo YOLO (compartido entre todas las cámaras)
    print("Cargando modelo YOLO...")
    model = YOLO("yolov8n.pt")
    
    # Cargar configuración de cámaras
    cameras_config = load_cameras_from_env()
    
    if not cameras_config:
        print("ERROR: No se encontraron cámaras en el archivo .env")
        print("Ejemplo de configuración en .env:")
        print("CAMERA_1=rtsp://192.168.1.100:554/stream1")
        print("CAMERA_2=rtsp://192.168.1.101:554/stream1")
        return
    
    print(f"Cámaras encontradas: {len(cameras_config)}")
    for name, config in cameras_config.items():
        print(f"  - {name}: {config['url']}")
    
    # Crear window manager y calcular layout
    window_manager = WindowManager()
    num_cameras = len(cameras_config)
    positions = window_manager.calculate_layout(num_cameras)
    
    print(f"Resolución de pantalla detectada: {window_manager.screen_width}x{window_manager.screen_height}")
    print(f"Tamaño de ventanas: {window_manager.window_size}")
    print(f"Distribución: {math.ceil(math.sqrt(num_cameras))}x{math.ceil(num_cameras/math.ceil(math.sqrt(num_cameras)))}")
    
    # Crear procesadores de cámara
    camera_processors = []
    camera_threads = []
    
    for i, (camera_name, config) in enumerate(cameras_config.items()):
        position = positions[i] if i < len(positions) else positions[0]
        processor = CameraProcessor(camera_name, config['url'], config['id'], window_manager, position)
        camera_processors.append(processor)
        
        # Crear hilo para cada cámara
        thread = threading.Thread(
            target=processor.process_camera, 
            args=(model,), 
            daemon=True,
            name=f"Camera-{camera_name}"
        )
        camera_threads.append(thread)
        thread.start()
    
    print("Todas las cámaras iniciadas. Presiona 'q' en cualquier ventana para salir.")
    
    try:
        # Esperar a que todos los hilos terminen
        for thread in camera_threads:
            thread.join()
    except KeyboardInterrupt:
        print("\nInterrupción detectada, cerrando sistema...")
    finally:
        # Detener todos los procesadores
        for processor in camera_processors:
            processor.stop_event.set()
        
        # Cerrar todas las ventanas
        cv2.destroyAllWindows()
        print("Sistema completamente cerrado")

if __name__ == "__main__":
    main()