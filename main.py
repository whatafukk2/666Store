import asyncio
import hashlib
import hmac
import json
import os
import sqlite3
import time
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qsl

from aiogram import Bot, Dispatcher, Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
