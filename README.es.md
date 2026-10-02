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

[`faustus-plugin.json`](faustus-plugin.json) permite al Hub descubrir y arrancar la app. Faustus accede a la API REST del editor mediante el puente MCP REST genérico indicado en el manifiesto. Plato **no** implementa `/api/agent/call`: el puente y el Hub se ocupan del descubrimiento y arranque.

### Eventos de exportación

Con `python app.py` en marcha, cuando termina un trabajo de exportación que incluye STL, Plato descomprime cada STL del paquete en `data/exports/<id del trabajo>/<familia>/` y anuncia cada uno en el bus de la familia como `plato.export.done {path, ref, title, format, job_id, document_id, layer_id, family}`; `ref` es `hoard://plato/export/<id del trabajo>` y `title` el nombre del documento y de la capa. La regla recomendada del Hub entrega el `path` a la biblioteca de modelos (`model_import_file`). La exportación no tiene formato 3MF, así que solo se anuncia STL. Los eventos de un mismo paquete se envían con 5,5 s de separación (`PLATO_EVENT_SPACING_S`) porque esa regla ignora una repetición dentro de su enfriamiento de 5 s. Plato escribe `data/mcp-token` al arrancar (el Hub identifica al emisor por él). Un vigilante en segundo plano detecta que el trabajo ha terminado, sin esperar a que el editor lo pregunte. Sin el Hub no cambia nada: los STL se descomprimen igualmente y el evento se descarta. La vista previa heredada `/api/process` no es una exportación y no emite nada.

`hoard_link/` es la biblioteca compartida de la familia, incluida sin cambios; solo se carga su módulo `family`, que usa únicamente la biblioteca estándar (con un nombre de paquete privado en `plato_family.py`), así que los requisitos de Plato no cambian.

## Verificación

```sh
python -m pytest tests/editor -q
python -m pytest tests -q
```

`implementation_status.json` documenta las tareas del editor terminadas y los resultados de prueba conocidos. La suite completa histórica registró un fallo previo de IoU para una estrella; comprueba la ejecución actual antes de afirmar que pasa. `npm test` es un marcador, no la suite Python.
