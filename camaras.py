import subprocess
import os
import threading
import time
import signal
import sys
import json
import uuid
from datetime import datetime
from dotenv import load_dotenv
import numpy as np
from ultralytics import YOLO
from collections import deque
import queue
import tempfile
import tkinter as tk
from tkinter import Canvas
from PIL import Image, ImageTk, ImageDraw, ImageFont
import logging
logging.getLogger('ultralytics').setLevel(logging.ERROR)

load_dotenv()

class VideoWindow:
    def __init__(self, camera_index, width=640, height=480):
        self.camera_index = camera_index
        self.root = tk.Toplevel()
        self.root.title(f"Cámara {camera_index}")
        self.root.geometry(f"{width}x{height}")
        
        self.canvas = Canvas(self.root, width=width, height=height)
        self.canvas.pack()
        
        self.current_image = None
        self.frame_queue = queue.Queue(maxsize=2)
        
    def queue_frame(self, frame_array):
        """Encolar frame para actualización thread-safe"""
        if self.frame_queue.full():
            try:
                self.frame_queue.get_nowait()
            except queue.Empty:
                pass
        try:
            self.frame_queue.put_nowait(frame_array)
        except queue.Full:
            pass
    
    def update_from_queue(self):
        """Actualizar frame desde la cola"""
        try:
            if not self.frame_queue.empty():
                frame_array = self.frame_queue.get_nowait()
                if frame_array is not None and frame_array.size > 0:  # Agregar esta validación
                    image = Image.fromarray(frame_array)
                    image = image.resize((640, 480), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(image)
                    
                    self.canvas.delete("all")
                    self.canvas.create_image(0, 0, anchor=tk.NW, image=photo)
                    self.current_image = photo
        except Exception as e:
            print(f"Error actualizando ventana cámara {self.camera_index}: {e}")
    
    def close(self):
        try:
            self.root.destroy()
        except:
            pass

class RTSPViewer:
    def __init__(self):
        # Configuración desde .env
        self.ip = os.getenv('CAMERA_IP', '192.168.1.100')
        self.ports = [int(port.strip()) for port in os.getenv('CAMERA_PORTS', '554').split(',')]
        self.vps_mode = os.getenv('VPS_MODE', 'false').lower() == 'true'
        self.show_window = os.getenv('SHOW_WINDOW', 'true').lower() == 'true'
        self.rtsp_path = os.getenv('RTSP_PATH', '/cam/realmonitor?channel=1&subtype=0')
        self.username = os.getenv('RTSP_USERNAME', 'admin')
        self.password = os.getenv('RTSP_PASSWORD', 'admin')
        
        # Estado del sistema
        self.processes = []
        self.threads = []
        self.running = False
        self.video_windows = {}
        self.tk_root = None
        
        # Configuración YOLO
        self.model = YOLO('yolov8n.pt')
        self.target_classes = [0, 2, 7, 16]  # personas, carros, camiones, perros
        self.class_names = {0: 'person', 2: 'car', 7: 'truck', 16: 'dog'}
        
        # Configuración grabación
        self.recording_buffer = 5  # segundos
        self.static_threshold = 30  # segundos
        self.frame_buffer_size = 150  # 5 segundos a 30fps
        self.frame_width = 640
        self.frame_height = 480
        self.frame_rate = 30
        
    def create_rtsp_url(self, port):
        """Crear URL RTSP con credenciales"""
        if self.username and self.password:
            return f"rtsp://{self.username}:{self.password}@{self.ip}:{port}{self.rtsp_path}"
        return f"rtsp://{self.ip}:{port}{self.rtsp_path}"
    
    def get_recording_path(self):
        """Crear directorio y nombre de archivo para grabación"""
        now = datetime.now()
        date_path = now.strftime("%Y/%m/%d")
        full_path = f"recordings/{date_path}"
        os.makedirs(full_path, exist_ok=True)
        
        date_str = now.strftime("%d%m%Y%H%M%S")
        uid = str(uuid.uuid4()).replace('-', '')[:10]
        filename = f"{date_str}_{uid}.mp4"
        
        return full_path, filename
    
    def frame_to_numpy(self, frame_data):
        """Convertir datos de frame a numpy array"""
        try:
            image = Image.frombytes('RGB', (self.frame_width, self.frame_height), frame_data)
            return np.array(image)
        except Exception as e:
            print(f"Error convirtiendo frame: {e}")
            return None
    
    def detect_objects(self, frame_array):
        """Detectar objetos con YOLO"""
        results = self.model(frame_array, classes=self.target_classes, conf=0.6)
        detections = []
        
        for result in results:
            if result.boxes is not None:
                for box in result.boxes:
                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
                    conf = box.conf[0].cpu().numpy()
                    cls = int(box.cls[0].cpu().numpy())
                    
                    if cls in self.class_names:
                        detections.append({
                            'class': self.class_names[cls],
                            'confidence': float(conf),
                            'bbox': [int(x1), int(y1), int(x2), int(y2)],
                            'center': [int((x1+x2)//2), int((y1+y2)//2)]  # Convertir explícitamente a int nativo de Python
                        })
        
        return detections
    
    def draw_detections(self, frame_array, detections):
        """Dibujar detecciones en el frame"""
        try:
            image = Image.fromarray(frame_array)
            draw = ImageDraw.Draw(image)
            
            for det in detections:
                x1, y1, x2, y2 = det['bbox']
                label = f"{det['class']}: {det['confidence']:.2f}"
                
                draw.rectangle([x1, y1, x2, y2], outline="green", width=2)
                draw.text((x1, y1-15), label, fill="green")
            
            return np.array(image)
        except Exception as e:
            print(f"Error dibujando detecciones: {e}")
            return frame_array
    
    def is_static_object(self, detections, previous_detections):
        """Verificar si los objetos son estáticos"""
        if not previous_detections:
            return False
        
        for det in detections:
            for prev_det in previous_detections:
                if det['class'] == prev_det['class']:
                    center_dist = np.sqrt((det['center'][0] - prev_det['center'][0])**2 + 
                                        (det['center'][1] - prev_det['center'][1])**2)
                    if center_dist < 50:
                        return True
        return False
    
    def save_recording(self, temp_file, detections_log, camera_index):
        """Guardar grabación usando FFmpeg"""
        if not os.path.exists(temp_file):
            return
        
        path, filename = self.get_recording_path()
        video_path = f"{path}/{filename}"
        json_path = f"{path}/{filename.replace('.mp4', '.json')}"
        
        # Comando FFmpeg
        cmd = [
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-f', 'rawvideo', '-pix_fmt', 'rgb24',
            '-s', f'{self.frame_width}x{self.frame_height}',
            '-r', str(self.frame_rate),
            '-i', temp_file,
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
            '-crf', '23', '-preset', 'fast',
            video_path
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode == 0:
                print(f"Grabación guardada: {video_path}")
                
                # Guardar metadatos - Asegurar que todos los valores sean serializables
                frame_count = os.path.getsize(temp_file) // (self.frame_width * self.frame_height * 3)
                
                # Limpiar detecciones para asegurar serialización JSON
                clean_detections = []
                for detection in detections_log:
                    clean_detection = {
                        'class': str(detection['class']),
                        'confidence': float(detection['confidence']),
                        'bbox': [int(x) for x in detection['bbox']],
                        'center': [int(x) for x in detection['center']]
                    }
                    clean_detections.append(clean_detection)
                
                metadata = {
                    'video_filename': filename,
                    'camera_index': int(camera_index),
                    'timestamp': datetime.now().isoformat(),
                    'detections': clean_detections,
                    'total_frames': int(frame_count),
                    'duration_seconds': float(frame_count / self.frame_rate)
                }
                
                with open(json_path, 'w') as f:
                    json.dump(metadata, f, indent=2)
                    
                print(f"Metadatos guardados: {json_path}")
            else:
                print(f"Error guardando video: {result.stderr.decode()}")
        except Exception as e:
            print(f"Error en FFmpeg: {e}")
        finally:
            if os.path.exists(temp_file):
                os.remove(temp_file)
    
    def detection_thread(self, detection_queue, result_queue, camera_index):
        """Hilo para procesar detecciones YOLO"""
        while self.running:
            try:
                frame_data = detection_queue.get(timeout=1)
                frame_array = self.frame_to_numpy(frame_data)
                if frame_array is not None:
                    detections = self.detect_objects(frame_array)
                    result_queue.put((frame_array, detections))
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Error en detección cámara {camera_index}: {e}")
    
    def camera_thread(self, port, camera_index):
        """Hilo principal para cada cámara"""
        rtsp_url = self.create_rtsp_url(port)
        
        # Comando FFmpeg
        cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-rtsp_transport', 'tcp', '-timeout', '5000000',
            '-i', rtsp_url,
            '-f', 'rawvideo', '-pix_fmt', 'rgb24',
            '-s', f'{self.frame_width}x{self.frame_height}',
            '-r', str(self.frame_rate),
            'pipe:1'
        ]
        
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.processes.append(process)
            print(f"✓ Cámara {camera_index + 1} conectada")
        except Exception as e:
            print(f"Error iniciando cámara {camera_index + 1}: {e}")
            return
        
        # Configurar detección
        detection_queue = queue.Queue(maxsize=5)
        result_queue = queue.Queue()
        
        detection_thread = threading.Thread(
            target=self.detection_thread,
            args=(detection_queue, result_queue, camera_index),
            daemon=True
        )
        detection_thread.start()
        
        # Variables de grabación
        frame_buffer = deque(maxlen=self.frame_buffer_size)
        recording = False
        last_detection_time = 0
        static_start_time = 0
        previous_detections = []
        detections_log = []
        temp_file = None
        temp_fd = None
        
        frame_count = 0
        frame_size = self.frame_width * self.frame_height * 3
        
        while self.running and process.poll() is None:
            try:
                # Leer frame
                frame_data = process.stdout.read(frame_size)
                if len(frame_data) != frame_size:
                    break
                
                frame_buffer.append(frame_data)
                frame_count += 1
                
                # Enviar para detección cada 5 frames
                if frame_count % 5 == 0 and detection_queue.empty():
                    try:
                        detection_queue.put_nowait(frame_data)
                    except queue.Full:
                        pass
                
                # Procesar resultados
                current_detections = []
                frame_array = None
                try:
                    while not result_queue.empty():
                        frame_array, detections = result_queue.get_nowait()
                        current_detections = detections
                except queue.Empty:
                    pass
                
                # Lógica de grabación
                if current_detections:
                    # Verificar si es estático
                    if self.is_static_object(current_detections, previous_detections):
                        if static_start_time == 0:
                            static_start_time = time.time()
                        elif time.time() - static_start_time > self.static_threshold:
                            if recording and temp_fd:
                                temp_fd.close()
                                self.save_recording(temp_file, detections_log, camera_index + 1)
                                recording = False
                                temp_file = None
                                temp_fd = None
                                detections_log = []
                                print(f"Objeto estático detectado, deteniendo grabación cámara {camera_index + 1}")
                            static_start_time = 0
                            previous_detections = []
                            continue
                    else:
                        static_start_time = 0
                    
                    last_detection_time = time.time()
                    
                    # Iniciar grabación
                    if not recording:
                        recording = True
                        detections_log = []
                        
                        temp_fd, temp_file = tempfile.mkstemp(suffix='.raw')
                        temp_fd = os.fdopen(temp_fd, 'wb')
                        
                        # Escribir buffer
                        for buffered_frame in frame_buffer:
                            temp_fd.write(buffered_frame)
                        
                        print(f"Iniciando grabación cámara {camera_index + 1}")
                    
                    detections_log.extend(current_detections)
                    previous_detections = current_detections
                
                # Continuar grabación
                if recording and temp_fd:
                    temp_fd.write(frame_data)
                    
                    # Detener si no hay detecciones
                    if time.time() - last_detection_time > self.recording_buffer:
                        temp_fd.close()
                        self.save_recording(temp_file, detections_log, camera_index + 1)
                        recording = False
                        temp_file = None
                        temp_fd = None
                        detections_log = []
                        previous_detections = []
                        print(f"Grabación terminada cámara {camera_index + 1}")
                
                # Mostrar video
                if self.show_window and not self.vps_mode and frame_array is not None:
                    if current_detections:
                        frame_array = self.draw_detections(frame_array, current_detections)
                    
                    if camera_index in self.video_windows:
                        self.video_windows[camera_index].queue_frame(frame_array)
                
            except Exception as e:
                print(f"Error procesando frame cámara {camera_index + 1}: {e}")
                break
        
        # Limpiar
        if temp_fd:
            temp_fd.close()
        if temp_file and os.path.exists(temp_file):
            os.remove(temp_file)
        
        process.terminate()
        process.wait()
    
    def test_connection(self, port):
        """Probar conexión RTSP"""
        rtsp_url = self.create_rtsp_url(port)
        print(f"Probando conexión a puerto {port}...")
        
        cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-rtsp_transport', 'tcp', '-timeout', '5000000',
            '-i', rtsp_url, '-frames:v', '1', '-f', 'null', '-'
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=20)
            if result.returncode == 0:
                print(f"✓ Conexión exitosa a puerto {port}")
                return True
            else:
                print(f"✗ Error en puerto {port}")
                return False
        except subprocess.TimeoutExpired:
            print(f"✗ Timeout en puerto {port}")
            # reintentar conexión
            try:
                result = subprocess.run(cmd, capture_output=True, timeout=20)
                if result.returncode == 0:
                    print(f"✓ Conexión exitosa a puerto {port}")
                    return True
                else:
                    print(f"✗ Error en puerto {port}")
                    return False
            except subprocess.TimeoutExpired:
                print(f"✗ Timeout en puerto {port}")
                return False
        except Exception as e:
            print(f"✗ Error en puerto {port}: {e}")
            return False

    def on_closing(self):
        """Manejar cierre de ventanas"""
        self.running = False
    
    def start_streaming(self):
        """Iniciar streaming de todas las cámaras"""
        print(f"Iniciando RTSP Viewer")
        print(f"Modo VPS: {self.vps_mode} | Mostrar ventanas: {self.show_window}")
        print(f"IP: {self.ip} | Puertos: {self.ports}")
        
        if not self.check_dependencies():
            return
        
        # Probar conexiones
        valid_ports = [port for port in self.ports if self.test_connection(port)]
        
        if not valid_ports:
            print("No se pudo conectar a ninguna cámara")
            return
        
        print(f"\nIniciando streaming en {len(valid_ports)} cámara(s)...")
        self.running = True

        # Crear ventanas si es necesario
        if self.show_window and not self.vps_mode:
            self.tk_root = tk.Tk()
            self.tk_root.withdraw()
            self.tk_root.protocol("WM_DELETE_WINDOW", self.on_closing)
            
            for i in range(len(valid_ports)):
                self.video_windows[i] = VideoWindow(i + 1, self.frame_width, self.frame_height)
                self.video_windows[i].root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # Crear hilos para cada cámara
        for i, port in enumerate(valid_ports):
            thread = threading.Thread(target=self.camera_thread, args=(port, i), daemon=True)
            thread.start()
            self.threads.append(thread)
            time.sleep(1)
        
        try:
            print("Sistema activo. Presiona Ctrl+C para salir")
            while self.running:
                if self.show_window and not self.vps_mode and self.tk_root:
                    try:
                        for window in self.video_windows.values():
                            window.update_from_queue()
                        self.tk_root.update()
                        time.sleep(0.033)  # ~30 FPS
                    except tk.TclError:
                        break
                else:
                    time.sleep(1)
        except KeyboardInterrupt:
            print("\nDeteniendo sistema...")
        finally:
            self.stop_streaming()
    
    def stop_streaming(self):
        """Detener streaming"""
        self.running = False
        
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                process.wait()
        
        for thread in self.threads:
            thread.join(timeout=2)

        for window in self.video_windows.values():
            window.close()
            
        if self.tk_root:
            try:
                self.tk_root.destroy()
            except:
                pass
        
        print("Sistema detenido")
    
    def check_dependencies(self):
        """Verificar dependencias"""
        try:
            import numpy as np
            from ultralytics import YOLO
            from PIL import Image, ImageTk
            import tkinter as tk
            
            result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                print("✗ FFmpeg no encontrado")
                return False
                
            print("✓ Dependencias encontradas")
            return True
        except ImportError as e:
            print(f"✗ Dependencias faltantes: {e}")
            return False
        except Exception as e:
            print(f"✗ Error verificando dependencias: {e}")
            return False
    
    def list_cameras(self):
        """Listar cámaras disponibles"""
        print("Escaneando cámaras...")
        available = [port for port in self.ports if self.test_connection(port)]
        
        if available:
            print(f"Cámaras disponibles: {available}")
        else:
            print("No se encontraron cámaras")
        
        return available

def signal_handler(sig, frame):
    """Manejador de señales"""
    print("\nRecibida señal de terminación...")
    sys.exit(0)

def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    viewer = RTSPViewer()
    
    if len(sys.argv) > 1 and sys.argv[1] == '--list':
        viewer.list_cameras()
        return
    
    viewer.start_streaming()

if __name__ == "__main__":
    main()