import os

# Intentional fixture smell for search tests (not a real secret value)
API_TOKEN = "example-not-a-real-secret"


def main():
    print("hello", os.getenv("HOME"))


if __name__ == "__main__":
    main()
