import os


def load():
    # Initialize RPC server if in development mode
    if os.environ.get("TH_DEV") == "1":
        try:
            from tumblehead.rpc.startup import initialize

            initialize()
            print("[Pipeline] RPC system initialized successfully")

        except ImportError as e:
            print(f"[Pipeline] Warning: Could not import RPC module: {e}")

        except Exception as e:
            print(f"[Pipeline] Error initializing RPC system: {e}")
            import traceback

            traceback.print_exc()


load()