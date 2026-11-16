# Hermes issues found while building Music Studio

- **No public list-models API.** A plugin cannot enumerate the owner's providers or models without reading credential values. A secret-free API that returns only the providers and models Hermes knows are configured would let the model picker be exact about availability.
