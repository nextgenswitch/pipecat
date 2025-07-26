# pipecat
A Pipecat AI Assistant integration example with Nextgenswitch
1. **Set up a virtual environment** (optional but recommended):

   ```sh
   python -m venv venv
   source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
   ```

2. **Install dependencies**:

   ```sh
   pip install -r requirements.txt
   ```
3. **Create .env**:
   Copy the example environment file and update with your settings:

   ```sh
   cp env.example .env
   ```
4. **Run bot.py**:
   ```
   python bot.py
   ``` 
5. **Create Custom function in Nextgenswitch**:


```
 <?xml version="1.0"?>
<response>
   <connect><stream name="stream" url="ws://pipecat_server_ip:8765/ws"/></connect> 
<say>Please enter the extension number  followed by the hash key</say>
  <pause length="120"/> 
</response> 
    
```
