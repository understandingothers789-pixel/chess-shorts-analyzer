# Capture and selection changes in 0.3.0

The previous local capture wrote one PNG per sample and automatically produced contact sheets. Opening a capture also loaded thumbnails for every row. This made fine intervals expensive in storage, memory, and time, even when only a few frames were relevant.

Local-video indexing now keeps timestamps and source frame numbers. The inspector uses the real source timestamps and verifies fast seeks before displaying a frame. If a seek is uncertain it decodes by frame number. Full-size preview and comparison still use the source pixels. Compact exports decode only the requested frames. A full-quality ZIP can include original pixels for sharing without the video.

Live and browser captures save compact JPEGs by default. Identical consecutive captures share a file, while each sample keeps its own timestamp. Original PNG capture remains available. Thumbnails are loaded near the visible gallery rows. Picks persist and can be used for contact sheets or ChatGPT export.

For the tightest timing, use a local source and select 0.01 seconds. A frame is included at most once. The recording's frame rate and presentation timestamps determine the actual time between frames.

On the first two seconds of the repository's synthetic sample, both capture modes produced 60 selectable frames at the 0.01-second setting. The PNG session used 3,496,851 bytes and the indexed session used 22,116 bytes. This measurement excludes the original video and any generated sheets or exports.
