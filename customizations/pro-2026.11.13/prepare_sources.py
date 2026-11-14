#!/usr/bin/env python3
"""Reproduce the paired enhancement sources in fresh directories."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parent


def prepare(core,pro):
    profile=json.loads((ROOT/'target-profile.json').read_text())
    if core.exists() or pro.exists():
        raise SystemExit('Choose two new directories; existing work is preserved')
    for destination,repository,commit in (
        (core,'git@github.com:AgentFlocks/flocksdev.git',profile['sourceCommit']),
        (pro,'git@github.com:AgentFlocks/flockspro.git',profile['proCommit'])):
        destination.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['git','clone','--filter=blob:none','--no-checkout',repository,str(destination)],check=True)
        checkout = ['checkout','-b',profile['developmentBranch'],commit] if destination == core else ['checkout','--detach',commit]
        subprocess.run(['git','-C',str(destination),*checkout],check=True)
    subprocess.run(['git','-C',str(core),'merge-base','--is-ancestor',profile['coreCommit'],profile['sourceCommit']],check=True)
    print('Prepared the pinned zw_dev Core and matching Pro source pair; install Core webui locked dependencies before building')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core',type=Path,required=True)
    parser.add_argument('--pro',type=Path,required=True)
    args=parser.parse_args()
    prepare(args.core.resolve(),args.pro.resolve())
