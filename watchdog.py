import os
import psutil
import time

class OmegaWatchdog:
    def __init__(self, cpu_threshold=85, ram_threshold=80):
        self.cpu_threshold = cpu_threshold
        self.ram_threshold = ram_threshold

    def monitor_resources(self):
        cpu_usage = psutil.cpu_percent(interval=1)
        ram_usage = psutil.virtual_memory().percent

        print(f"[Omega Log] CPU: {cpu_usage}% | RAM: {ram_usage}%")

        if cpu_usage > self.cpu_threshold or ram_usage > self.ram_threshold:
            self.throttle_ecosystem()
        else:
            self.release_throttling()

    def throttle_ecosystem(self):
        print("⚠️ [Critical] Resource Ceiling Hit. Throttling NexusAgents...")
        os.system("pkill -STOP -f 'NexusAgent'")

    def release_throttling(self):
        # We only want to resume if they were actually stopped
        os.system("pkill -CONT -f 'NexusAgent'")

if __name__ == "__main__":
    print("🚀 Omega Watchdog Active. Monitoring Termux resources...")
    watchdog = OmegaWatchdog()
    try:
        while True:
            watchdog.monitor_resources()
            time.sleep(5)
    except KeyboardInterrupt:
        print("\n[Shutting down Watchdog]")
