# Ransom-Doors

My attempt at recreating Ransom from the new Archives update in Doors (Roblox).

It focuses more on the horror side of things, while trying to be as accurate as possible to Ransom.

<img width="2559" height="1439" alt="image" src="https://github.com/user-attachments/assets/d24b2870-d082-4519-b2e9-d0de887d9c7f" />

# Features:

# The minigame itself
  - Instead of dragging .coin files to the ransom window, you have to open drop-down menus on randomly-spawning windows and see if it has a coin in it or not.
  - Corrupted entries can spawn which make you lost instantly
  - The glitch windows teleport around the screen to add difficulty.
# Some (potentially dangerous) stuff this file does
  - "Encrypts" your files by making a shortcut with the iconic Ransom stop sign and moving the files to a safe folder called RansomIconData in your AppData folder
    - They come back after you win, if you lose, simply turn your computer back on and play it again.

# What happens if you lose?
  - If you had any flash drives/CDs/DVDs inserted while the ransomware ran, the software will remember which drives were inserted at the time.
  - It will put cd_1.exe into your startup folder, which will check if any inserted media is what it remembers. If it is, it will make you **watch, and create a new program.**
    - It deletes itself after it does its thing.

# What if you can't win the game, no matter how hard you try?
Simply run _RansomRecovery.exe_. Since Ransom leaves behind a JSON pointing to the locations of the original files in the RansomIconState folder in the Local Folder of AppData (in whatever user you ran it as). Per my experiments with other computers, it has worked on all of them.
However, **still remain cautious, as I cannot absolutely, 100% guarantee that it will not fail** (even though it hasn't failed on other devices)

## This software only runs on Windows.

> [!CAUTION]
> This file is NOT to be messed with. I HIGHLY suggest to use a virtual machine/an old device with Windows that you don't use much anymore to toy around with this Ransom. You have been warned.

> [!NOTE]
> Yeah, this program was maybe, just a tad bit, err, let's say, 75% vibe-coded? BUT, I know how all of this works, and it's not just because I ran it on my own computer (without a VM) all the time. If you want to know what LLM's I consulted for all this, I used ChatGPT initially, but then switched to DeepSeek.

# You can do whatever with this, just give me credit.
