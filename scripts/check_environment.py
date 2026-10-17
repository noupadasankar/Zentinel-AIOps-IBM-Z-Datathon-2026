from importlib.metadata import version

from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

packages = [
    "drain3",
    "numpy",
    "pandas",
    "scikit-learn",
    "fastapi",
    "streamlit",
    "plotly",
    "pytest",
]

print("\n=== Installed package versions ===")

for package in packages:
    print(f"{package}: {version(package)}")

print("\n=== Drain3 log parsing test ===")

config = TemplateMinerConfig()
config.drain_sim_th = 0.4
config.drain_depth = 4

miner = TemplateMiner(config=config)

messages = [
    "INFO worker connected to 10.0.0.1",
    "INFO worker connected to 10.0.0.2",
    "ERROR disk read failed on /dev/sda",
]

for message in messages:
    result = miner.add_log_message(message)
    print(f"Cluster ID: {result['cluster_id']} | Template: {result['template_mined']}")

print("\nSUCCESS: Environment and Drain3 test completed.")
