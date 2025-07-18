#!/data/data/com.termux/files/usr/bin/bash

CAM_PORT=554

PUERTOS_LOCALES=(8451 8453 8455 8457 8459)

RANGO_IPS="192.169.0.0-120"

function escanear_camaras() {
  echo "Escaneando IPs activas con puerto $CAM_PORT abierto en $RANGO_IPS..."
  nmap -p $CAM_PORT --open $RANGO_IPS -oG - | awk '/554\/open/{print $2}'
}

function redirigir_a_camaras() {
  local index=0
  local ip

  for ip in $(escanear_camaras); do
    if [[ $index -ge ${#PUERTOS_LOCALES[@]} ]]; then
      echo "⚠️  Se detectaron más de ${#PUERTOS_LOCALES[@]} cámaras. Solo se redireccionan las primeras 5."
      break
    fi

    local puerto_local=${PUERTOS_LOCALES[$index]}
    echo "🔁 Redirigiendo puerto local $puerto_local → $ip:$CAM_PORT"
    nohup socat TCP-LISTEN:$puerto_local,fork TCP:$ip:$CAM_PORT >/dev/null 2>&1 &
    echo "✅ Redirección activa para $ip en puerto local $puerto_local (PID $!)"
    ((index++))
  done

  if [[ $index -eq 0 ]]; then
    echo "❌ No se encontraron cámaras con el puerto $CAM_PORT abierto en el rango $RANGO_IPS."
  fi
}

function menu() {
  while true; do
    echo "====== Redirección automática de cámaras RTSP ======"
    echo "1) Buscar cámaras y redirigir automáticamente (máx 5)"
    echo "2) Salir"
    read -rp "Elige una opción: " opt

    case "$opt" in
      1) redirigir_a_camaras ;;
      2) exit 0 ;;
      *) echo "Opción inválida, intenta de nuevo." ;;
    esac
    echo
  done
}

menu
