# Plato's Hoard

[English](README.md)

Editor local para convertir imágenes 2D en siluetas editables y relieve 3D imprimible. Sube un PNG, ajusta las capas en el editor del navegador y exporta la silueta negra, un SVG vectorial o un STL extruido. El conversor original de una sola operación sigue disponible en `/legacy`.

## Arranque local

Necesita Python 3 y las dependencias de `requirements.txt`.

```sh
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

Abre [http://127.0.0.1:5000/editor](http://127.0.0.1:5000/editor). En otros sistemas, usa `.venv/bin/python`. Flask guarda documentos y trabajos del editor bajo `data/` y limita las subidas a 16 MB.

## Faustus y Hoard Hub

[`faustus-plugin.json`](faustus-plugin.json) permite al Hub descubrir y arrancar la app. Faustus accede a la API REST del editor mediante el puente MCP REST genérico indicado en el manifiesto. Plato **todavía no** implementa `/api/agent/call`, token de agente ni publicación de eventos del Hub; el puente y el Hub se ocupan del descubrimiento y arranque.

## Verificación

```sh
python -m pytest tests/editor -q
python -m pytest tests -q
```

`implementation_status.json` documenta las tareas del editor terminadas y los resultados de prueba conocidos. La suite completa histórica registró un fallo previo de IoU para una estrella; comprueba la ejecución actual antes de afirmar que pasa. `npm test` es un marcador, no la suite Python.
