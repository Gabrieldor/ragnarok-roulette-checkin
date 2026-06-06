import queue
import threading
import pydirectinput
from pynput import keyboard

click_queue = queue.Queue()

def worker():
    while True:
        click_queue.get()
        pydirectinput.click()

threading.Thread(target=worker, daemon=True).start()

def on_press(key):
    char = getattr(key, 'char', None)
    if char in '1234567890':
        click_queue.put(1)

with keyboard.Listener(on_press=on_press) as l:
    l.join()
