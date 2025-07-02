import subprocess, cv2, numpy as np, threading, queue, time, datetime, json, os, secrets, string, math, tkinter as tk
from ultralytics import YOLO
from collections import deque
from dotenv import load_dotenv

load_dotenv()

class WindowManager:
    def __init__(self, show_windows=True):
        self.show_windows = show_windows
        self.screen_width, self.screen_height = self.get_screen_size()
        self.window_positions, self.window_size = {}, None
        
    def get_screen_size(self):
        if not self.show_windows: return 1920, 1080
        try:
            root = tk.Tk()
            width, height = root.winfo_screenwidth(), root.winfo_screenheight() - 50
            root.destroy()
            return width, height
        except: return 1920, 1030
    
    def calculate_layout(self, num_cameras):
        if not self.show_windows: return [(0, 0)] * num_cameras
        
        if num_cameras > 9:
            cols = rows = math.ceil(math.sqrt(num_cameras))
        else:
            layout = {
                1: (1,1), 2: (2,1), 3: (2,2), 4: (2,2), 5: (3,2), 6: (3,2), 7: (3,3), 8: (3,3), 9: (3,3)
            }.get(num_cameras, (2,2))
            cols, rows = layout
        
        margin = 50
        window_width = (self.screen_width - margin * (cols + 1)) // cols
        window_height = (self.screen_height - margin * (rows + 1)) // rows
        
        # Mantener aspect ratio 16:9
        target_ratio = 16/9
        if window_width / window_height > target_ratio:
            window_width = int(window_height * target_ratio)
        else:
            window_height = int(window_width / target_ratio)
        
        self.window_size = (window_width, window_height)
        return [(margin + (i % cols) * (window_width + margin), 
                margin + (i // cols) * (window_height + margin)) for i in range(num_cameras)]
    
    def set_window_position(self, window_name, position):
        if not self.show_windows:
            print(f"[GUI] Ventana virtual configurada: {window_name}")
            return
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, *self.window_size)
        cv2.moveWindow(window_name, *position)

class CameraProcessor:
    def __init__(self, camera_name, rtsp_url, camera_id, window_manager, window_position, show_windows=True):
        self.camera_name, self.rtsp_url, self.camera_id = camera_name, rtsp_url, camera_id
        self.window_manager, self.window_position, self.show_windows = window_manager, window_position, show_windows
        self.width, self.height, self.fps = 1280, 720, 20
        
        # Queues y threading
        self.frame_queue = queue.Queue(maxsize=2)
        self.detection_queue = queue.Queue(maxsize=5)
        self.stop_event = threading.Event()
        
        # Variables de estado
        self.detections, self.recording, self.last_detection_time = [], False, 0
        self.video_writer, self.detection_log, self.current_video_path = None, [], None
        self.conf_threshold = 0.55
        
        # Modelo YOLO
        self.yolo_model, self.model_loaded = None, False
        
        # Buffers y estadísticas
        self.prebuffer = deque(maxlen=self.fps * 5)  # 5 segundos de prebuffer
        self.seconds_to_stop_before_last_detection = 5
        self.frame_count = self.detection_count = 0
        self.last_stats_time, self.stats_interval = time.time(), 30
        
        # FFmpeg
        self.command = ["ffmpeg", "-rtsp_transport", "tcp", "-i", self.rtsp_url, 
                       "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        self.process = None
        
        print(f"[{self.camera_name}] Configurada - URL: {self.rtsp_url} | GUI: {'Habilitada' if self.show_windows else 'Deshabilitada'}")

    def load_yolo_model(self):
        try:
            print(f"[{self.camera_name}] Cargando modelo YOLO...")
            self.yolo_model = YOLO("yolov8n.pt")
            self.model_loaded = True
            print(f"[{self.camera_name}] Modelo YOLO cargado exitosamente")
        except Exception as e:
            print(f"[{self.camera_name}] Error cargando modelo YOLO: {e}")

    def generate_uid(self, length=10):
        return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(length))

    def print_stats(self):
        current_time = time.time()
        elapsed = current_time - self.last_stats_time
        fps_actual = self.frame_count / elapsed if elapsed > 0 else 0
        
        print(f"[{self.camera_name}] STATS: Frames: {self.frame_count} | FPS: {fps_actual:.1f} | "
              f"Detecciones: {self.detection_count} | Grabando: {'SÍ' if self.recording else 'NO'} | "
              f"Última detección: {time.time() - self.last_detection_time:.1f}s | Queue: {self.frame_queue.qsize()}/{self.frame_queue.maxsize}")
        
        self.frame_count = self.detection_count = 0
        self.last_stats_time = current_time

    def yolo_worker(self):
        if not self.model_loaded:
            print(f"[{self.camera_name}] YOLO Worker: Modelo no cargado, terminando...")
            return
            
        print(f"[{self.camera_name}] YOLO Worker iniciado")
        
        while not self.stop_event.is_set():
            try:
                frame = self.frame_queue.get(timeout=1)
                start_time = time.time()
                results = self.yolo_model(frame, conf=self.conf_threshold, iou=0.45, verbose=False)[0]
                processing_time = time.time() - start_time
                
                detections = []
                if results.boxes is not None:
                    for box in results.boxes:
                        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                        conf = float(box.conf[0].cpu().numpy())
                        cls = int(box.cls[0].cpu().numpy())
                        label = self.yolo_model.names[cls]
                        detections.append((int(x1), int(y1), int(x2), int(y2), label, conf))
                
                detection_result = {
                    'detections': detections, 'timestamp': time.time(), 'processing_time': processing_time
                }
                
                if not self.detection_queue.full():
                    self.detection_queue.put_nowait(detection_result)
                
                valid_detections = [d for d in detections if d[5] >= self.conf_threshold]
                if valid_detections:
                    self.detection_count += len(valid_detections)
                    for *_, label, conf in valid_detections:
                        print(f"[{self.camera_name}] Detectado: {label} con {conf*100:.2f}% de confianza (procesado en {processing_time*1000:.1f}ms)")
                
                self.frame_queue.task_done()
                
            except queue.Empty: continue
            except Exception as e: print(f"[{self.camera_name}] YOLO Worker ERROR: {e}")
                        
        print(f"[{self.camera_name}] YOLO Worker terminado")

    def start_recording(self):
        self.detection_log = []
        now = datetime.datetime.now()
        folder_path = os.path.join("recordings", str(now.year), f"{now.month:02d}", f"{now.day:02d}")
        os.makedirs(folder_path, exist_ok=True)

        filename = f"detection_{now.strftime('%Y%m%d_%H%M%S')}_{self.generate_uid()}.mp4"
        self.current_video_path = os.path.join(folder_path, filename)

        fourcc = cv2.VideoWriter_fourcc(*'avc1')
        self.video_writer = cv2.VideoWriter(self.current_video_path, fourcc, self.fps, (self.width, self.height))
        
        if not self.video_writer.isOpened():
            print(f"[{self.camera_name}] Error: No se pudo inicializar VideoWriter con avc1, probando con mp4v...")
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            self.video_writer = cv2.VideoWriter(self.current_video_path, fourcc, self.fps, (self.width, self.height))
        
        print(f"[{self.camera_name}] Iniciando grabación: {self.current_video_path}")
        for buffered_frame in self.prebuffer:
            self.video_writer.write(buffered_frame)

    def stop_recording(self):
        if self.video_writer:
            self.video_writer.release()
            self.video_writer = None
            print(f"[{self.camera_name}] Grabación detenida")

            if self.current_video_path:
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
        try:
            base_name = os.path.splitext(video_path)[0]
            optimized_path = f"{base_name}_web.mp4"
            
            ffmpeg_cmd = ["ffmpeg", "-y", "-i", video_path, "-c:v", "libx264", "-preset", "fast", 
                         "-crf", "23", "-profile:v", "main", "-level", "3.1", "-pix_fmt", "yuv420p",
                         "-movflags", "+faststart", "-maxrate", "2M", "-bufsize", "4M", 
                         "-g", str(self.fps * 2), "-sc_threshold", "0", optimized_path]
            
            print(f"[{self.camera_name}] Optimizando video para web: {optimized_path}")
            result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"[{self.camera_name}] Video optimizado guardado: {optimized_path}")
            else:
                print(f"[{self.camera_name}] Error al optimizar video: {result.stderr}")
                
        except Exception as e:
            print(f"[{self.camera_name}] Error en optimización: {e}")

    def process_camera(self):
        self.load_yolo_model()
        if not self.model_loaded:
            print(f"[{self.camera_name}] No se pudo cargar el modelo YOLO, terminando...")
            return
        
        yolo_thread = threading.Thread(target=self.yolo_worker, daemon=True, name=f"YOLO-{self.camera_name}")
        yolo_thread.start()
        
        window_name = f"Camara - {self.camera_name}"
        if self.show_windows:
            self.window_manager.set_window_position(window_name, self.window_position)
        
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
                self.frame_count += 1

                if not self.frame_queue.full():
                    self.frame_queue.put_nowait(frame.copy())

                # Procesar resultados de detección
                try:
                    while not self.detection_queue.empty():
                        detection_result = self.detection_queue.get_nowait()
                        self.detections = detection_result['detections']
                        
                        valid_detections = [d for d in self.detections if d[5] >= self.conf_threshold]
                        if valid_detections:
                            self.last_detection_time = detection_result['timestamp']
                except queue.Empty: pass

                # Crear frame anotado
                annotated = None
                if self.show_windows or self.recording:
                    annotated = frame.copy()
                    
                    for x1, y1, x2, y2, label, conf in self.detections:
                        if conf >= self.conf_threshold:
                            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
                            cv2.putText(annotated, f"{label} {conf*100:.1f}%", (x1, y1-10),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

                    if self.recording:
                        cv2.putText(annotated, "GRABANDO", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                    
                    cv2.putText(annotated, f"{self.camera_name} | Q:{self.frame_queue.qsize()}", 
                               (10, self.height - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

                buffer_frame = annotated if annotated is not None else frame
                self.prebuffer.append(buffer_frame.copy())

                # Guardar detecciones
                if self.recording and self.detections:
                    timestamp = datetime.datetime.now().isoformat()
                    self.detection_log.extend([{
                        "timestamp": timestamp, "label": label, "confidence": round(conf, 3), "box": [x1, y1, x2, y2]
                    } for x1, y1, x2, y2, label, conf in self.detections if conf >= self.conf_threshold])

                # Control de grabación
                current_time = time.time()
                if not self.recording and (current_time - self.last_detection_time) < 1:
                    self.recording = True
                    self.start_recording()
                elif self.recording and (current_time - self.last_detection_time) >= self.seconds_to_stop_before_last_detection:
                    self.recording = False
                    self.stop_recording()

                if self.recording and self.video_writer and annotated is not None:
                    self.video_writer.write(annotated)

                # Mostrar ventana o estadísticas
                if self.show_windows and annotated is not None:
                    cv2.imshow(window_name, annotated)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
                elif current_time - self.last_stats_time >= self.stats_interval:
                    self.print_stats()

        except Exception as e:
            print(f"[{self.camera_name}] Error en procesamiento: {e}")
        finally:
            if self.recording: self.stop_recording()
            self.stop_event.set()
            if self.process: self.process.terminate()
            if self.show_windows: cv2.destroyWindow(window_name)
            print(f"[{self.camera_name}] Sistema detenido")

def load_cameras_from_env():
    cameras, camera_id = {}, 1
    for key, value in os.environ.items():
        if key.startswith('CAMERA_') and value:
            cameras[key] = {'url': value, 'id': camera_id}
            camera_id += 1
    return cameras

def main():
    show_windows = os.getenv('SHOW_WINDOW_ENV', 'true').lower() in ['true', '1', 'yes', 'on']
    mode_text = "con interfaz gráfica" if show_windows else "modo VPS (sin GUI)"
    print(f"Iniciando sistema multi-cámara YOLO optimizado {mode_text}...")
    print("MEJORAS: Cada cámara tiene su propio modelo YOLO en hilos separados")
    
    cameras_config = load_cameras_from_env()
    if not cameras_config:
        print("ERROR: No se encontraron cámaras en el archivo .env")
        print("Ejemplo de configuración en .env:")
        print("CAMERA_1=rtsp://192.168.1.100:554/stream1")
        print("CAMERA_2=rtsp://192.168.1.101:554/stream1")
        print("SHOW_WINDOW_ENV=false  # Para modo VPS sin GUI")
        return
    
    print(f"Cámaras encontradas: {len(cameras_config)}")
    for name, config in cameras_config.items():
        print(f"  - {name}: {config['url']}")
    
    window_manager = WindowManager(show_windows=show_windows)
    positions = window_manager.calculate_layout(len(cameras_config))
    
    if show_windows:
        print(f"Resolución de pantalla detectada: {window_manager.screen_width}x{window_manager.screen_height}")
        print(f"Tamaño de ventanas: {window_manager.window_size}")
    else:
        print("Modo VPS activado - Las ventanas están deshabilitadas")
        print("El sistema seguirá procesando, grabando y detectando objetos")
        print("Las estadísticas se mostrarán cada 30 segundos por cámara")
    
    camera_processors, camera_threads = [], []
    
    print(f"\nCargando {len(cameras_config)} modelos YOLO independientes...")
    
    for i, (camera_name, config) in enumerate(cameras_config.items()):
        position = positions[i] if i < len(positions) else positions[0]
        processor = CameraProcessor(camera_name, config['url'], config['id'], 
                                  window_manager, position, show_windows=show_windows)
        camera_processors.append(processor)
        
        thread = threading.Thread(target=processor.process_camera, daemon=True, name=f"Camera-{camera_name}")
        camera_threads.append(thread)
        thread.start()
    
    print("Todas las cámaras iniciadas. Presiona 'q' en cualquier ventana para salir." if show_windows 
          else "Todas las cámaras iniciadas en modo VPS. Presiona Ctrl+C para salir.")
    print(f"Memoria optimizada: {len(cameras_config)} modelos YOLO ejecutándose en paralelo")
    
    try:
        for thread in camera_threads:
            thread.join()
    except KeyboardInterrupt:
        print("\nInterrupción detectada, cerrando sistema...")
    finally:
        for processor in camera_processors:
            processor.stop_event.set()
        if show_windows: cv2.destroyAllWindows()
        print("Sistema completamente cerrado")

if __name__ == "__main__":
    main()