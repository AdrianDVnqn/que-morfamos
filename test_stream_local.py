import json
import urllib.request
import time
import sys

# Force output encoding to utf-8 to avoid console issues
sys.stdout.reconfigure(encoding='utf-8')

URL = "http://127.0.0.1:8000/chat/stream"

def test_stream_urllib(query):
    print(f"\n--- Testing Query: {query} ---")
    data = {
        "query": query,
        "conversation_context": {},
        "tone": "cordial"
    }
    json_data = json.dumps(data).encode('utf-8')
    req = urllib.request.Request(URL, data=json_data, headers={'Content-Type': 'application/json'})
    
    try:
        with urllib.request.urlopen(req) as resp:
            print("Status Code:", resp.getcode())
            for line in resp:
                decoded_line = line.decode('utf-8').strip()
                if decoded_line:
                    try:
                        event = json.loads(decoded_line)
                        type_ = event.get("type")
                        if type_ == "token":
                            print(event.get("content"), end="", flush=True)
                        elif type_ == "meta":
                            mode = event.get("mode")
                            print(f"\n[META] Mode: {mode}")
                        elif type_ == "error":
                            print(f"\n[ERROR] {event.get('message')}")
                    except json.JSONDecodeError:
                        print(f"\n[RAW] {decoded_line}")
            print("\n--- End of Stream ---")
    except Exception as e:
        print(f"Request Error: {e}")

if __name__ == "__main__":
    print("Waiting for server to warmup...")
    time.sleep(5) 
    test_stream_urllib("hola")
    time.sleep(1)
    test_stream_urllib("lugares con pelotero")
