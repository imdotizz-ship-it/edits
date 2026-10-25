# Jugg edit (COD highlight montage)

Pipeline em Python + ffmpeg que transforma gameplay vertical num edit estilo jugg/velocity para TikTok (1080x1920, 60fps).

- `render.py`: roteiro de cortes sincronizado nas batidas (143,58 BPM, drop em 39,47s da faixa), velocity ramps, slow motion por fluxo óptico, shake, zoom punch, câmera pseudo-3D, transições (zoom, whip, spin 3D, glitch), glow, aberração cromática, flashes e color grade.
- `audio.py`: corta o trecho da música e adiciona sub-boom no drop, riser na quebra e whooshes nos cortes.
- `build.sh CLIP.mp4 MUSICA.wav PASTA_TRABALHO SAIDA.mp4`: roda tudo.

Dependências: `ffmpeg`, `pip install opencv-python-headless numpy scipy soundfile librosa`.
