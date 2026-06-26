import sys
import os

# add parent directory to path so we can import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from create_data_source import create_data_source
from create_index import create_index
from create_skillset import create_skillset
from create_indexer import create_indexer


if __name__ == "__main__":
    print("=" * 50)
    print("Setting up Azure AI Search for HomeSite Claims")
    print("=" * 50)

    print("\n1. Creating data source...")
    create_data_source()

    print("\n2. Creating index...")
    create_index()

    print("\n3. Creating skillset...")
    create_skillset()

    print("\n4. Creating indexer...")
    create_indexer()

    print("\n" + "=" * 50)
    print("Setup complete!")
    print("=" * 50)
    print("\nNext steps:")
    print("1. Go to portal → homesite-claims-search → Indexers")
    print("2. Click policy-indexer → watch status → Succeeded")
    print("3. Go to Indexes → policy-index → Search explorer")
    print("4. Click Search — you should see your chunks with vectors")
    print("5. Come back and run: python main.py cli")