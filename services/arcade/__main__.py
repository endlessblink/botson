"""Explicit local demo; production serving is a separate authorized deployment."""
import argparse
import tempfile
from pathlib import Path

import uvicorn
import yaml
from .app import ROOT, create_app


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--demo',action='store_true',required=True)
    args=parser.parse_args()
    cfg=yaml.safe_load((ROOT/'config/arcade.yaml').read_text())['service']
    with tempfile.TemporaryDirectory(prefix='botson-arcade-demo-') as directory:
        app=create_app(cfg,db_path=Path(directory)/'demo.db',demo=args.demo)
        uvicorn.run(app,host='127.0.0.1',port=cfg['port'],access_log=False)


if __name__=='__main__': main()
