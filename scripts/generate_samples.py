"""Generate redistributable synthetic footage, sheets and a full analysis ZIP."""
import argparse
import subprocess
from pathlib import Path

from chess_shorts.capture.local import extract
from chess_shorts.export import export_chatgpt
from chess_shorts.session import Session
from chess_shorts.sheets import SheetConfig, generate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('samples'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    video = args.output / 'synthetic_test.mp4'
    if not video.exists():
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=size=360x640:rate=30:duration=5', '-c:v', 'libx264',
                        '-threads', '2', '-pix_fmt', 'yuv420p', str(video.resolve())], check=True)
    session = Session.create(args.output, 'local', .5, str(video.resolve()))
    extract(video, session)
    generate(session, SheetConfig())
    result = export_chatgpt(session)
    print(result)


if __name__ == '__main__':
    main()
