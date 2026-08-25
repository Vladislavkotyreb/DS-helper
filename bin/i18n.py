#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Two-language message helper.

The engine ships in English by default; set "lang": "ru" in config.json to get
Russian reports and CLI output. One codebase, no forked translations.

Usage:
    from i18n import t
    print(t('расхождений: %d', 'findings: %d') % n)
"""
import json, os

_LANG = None


def lang():
    global _LANG
    if _LANG is None:
        _LANG = os.environ.get('NW_LANG', '')
        if not _LANG:
            here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            try:
                cfg = json.load(open(os.path.join(here, 'config.json'), encoding='utf-8'))
                _LANG = cfg.get('lang', 'en')
            except Exception:
                _LANG = 'en'
    return _LANG


def t(ru, en):
    return ru if lang() == 'ru' else en
