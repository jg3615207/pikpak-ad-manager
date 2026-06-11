# The 8KB-Adler32 Partial Hashing Algorithm for Large Media Files

## Abstract
Traditional cryptographic hashing algorithms (like SHA-256 or MD5) scale linearly with file size, making them impractically slow for personal media libraries containing thousands of multi-gigabyte files. This paper details a "Fast Hash" heuristic algorithm that computes a 32-bit identifier for large files in near-constant time $\mathcal{O}(1)$. By applying an Adler-32 checksum strictly to the file's length, its first 4,096 bytes, and its last 4,096 bytes, the algorithm achieves instantaneous deduplication and file-move tracking with highly acceptable accuracy for non-secure environments.

---

## 1. Introduction: The I/O Bottleneck
When managing media collections, software must frequently scan directories to detect if a file is new, has been modified, or was simply renamed/moved. 

Hashing is the standard method for this. However, traditional hashes read a file from start to finish. On a standard mechanical Hard Disk Drive (HDD) reading at 100 MB/s, hashing a single 10 GB video file takes roughly 100 seconds. Hashing a library of 1,000 such files would take over 27 hours. The bottleneck is entirely bound by disk I/O, not CPU processing speed.

To circumvent this, partial-file hashing algorithms read only the most statistically unique portions of a file.

## 2. The Algorithm Definition
The method operates on the premise that media files (such as `.mp4` or `.mkv` containers) store highly variable, unique data at the beginning (headers, metadata, codec information) and at the end (indexes, footers). The middle of the file consists of raw frame data which is unnecessary to read if the headers and footers match.

### 2.1. The Formula
The algorithm maintains a continuous state using the **Adler-32** checksum function, updating the state in three sequential steps:

1. **Initialize & Seed:** Start the Adler-32 state at `1`. Feed the total file length (in little-endian bytes) into the hash.
2. **Head Chunk:** Read exactly 4,096 bytes (4KB) from the beginning of the file (offset 0). Feed this buffer into the hash.
3. **Tail Chunk:** If the file is larger than 4,096 bytes, seek to `File_Length - 4096`. Read the last 4,096 bytes of the file and feed this buffer into the hash.

The final 32-bit integer is then formatted as an 8-character zero-padded hexadecimal string.

## 3. Performance Characteristics
* **Time Complexity:** $\mathcal{O}(1)$ with respect to file size. The algorithm always reads exactly 8,192 bytes, whether the file is 10 Megabytes or 100 Gigabytes.
* **Disk I/O:** Virtually instantaneous. Reading 8KB from a modern disk takes fractions of a millisecond.
* **CPU Overhead:** Adler-32 was explicitly designed for speed, utilizing simple addition and modulo operations. It is significantly faster to compute than CRC32.

## 4. Collision Mathematics & Accuracy
Because the algorithm produces a 32-bit hash, there are exactly $2^{32}$ (4,294,967,296) possible hash values. 

### 4.1. The Birthday Paradox
In probability theory, the birthday paradox dictates that the probability of a collision (two different files producing the same hash) reaches 50% at roughly $\sqrt{N}$ items, where $N$ is the total number of possible hashes. 
For a 32-bit hash:
$$\sqrt{4,294,967,296} \approx 65,536$$

If a media library contains **65,536 files**, there is a ~50% chance that at least two files will share the same hash. For libraries containing less than 10,000 files, the collision risk remains comfortably below 1%.

### 4.2. Algorithmic Blind Spots (The "Ignored Middle")
If two files have the exact same file size, the exact same first 4KB, and the exact same last 4KB, they will produce an identical hash. If the internal video frames are altered without changing the file length or the headers/footers, the algorithm cannot detect the change. 

## 5. Implementation Reference
Below is a generic pseudocode implementation of the algorithm.

```python
import os
import zlib
import struct

def calculate_fast_hash(file_path):
    # Get file size
    file_size = os.path.getsize(file_path)
    
    # If empty file, return zero
    if file_size == 0:
        return "00000000"
        
    # Step 1: Initialize Adler-32 with the file size (8-byte little endian)
    size_bytes = struct.pack("<Q", file_size)
    hash_val = zlib.adler32(size_bytes, 1)
    
    with open(file_path, 'rb') as f:
        # Step 2: Read and hash the first 4KB
        head_data = f.read(4096)
        hash_val = zlib.adler32(head_data, hash_val)
        
        # Step 3: Read and hash the last 4KB
        if file_size > 4096:
            offset = max(4096, file_size - 4096)
            f.seek(offset)
            tail_data = f.read(4096)
            hash_val = zlib.adler32(tail_data, hash_val)
            
    # Format as 8-character lowercase hex, masking to 32-bit
    return f"{(hash_val & 0xFFFFFFFF):08x}"
```

> [!CAUTION]
> **Security Warning**
> This algorithm provides **zero cryptographic security**. It is trivial for a malicious actor to craft a forged file that perfectly matches a target hash. Never use this algorithm for validating software packages, verifying monetary transactions, or deduplicating sensitive server data.

## 6. Conclusion and Use Cases
The 8KB-Adler32 method is a highly specialized heuristic.

**When to use it:**
* Personal media managers (like Plex, Jellyfin, or local catalogers).
* Detecting moved or renamed videos on a local NAS.
* Rapidly indexing large, append-only datasets where security is not a factor.

**When NOT to use it:**
* Cryptographic file verification.
* Bit-rot detection (since corruption in the middle of the file is ignored).
* Deduplicating systems handling billions of small files (due to the 32-bit collision limit).
