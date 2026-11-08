# make          -> build/td-h3.bin   (TX limited to the bands in radio.c)
# make rxonly   -> build/td-h3-rx.bin (no transmit at all: flash this one first)
# make test     -> build both and run test_fw.py (T="scan fm" for just those tests), then the budget
# make budget   -> tools/budget.py: free space, reserve and price of each scarce resource
SDCC_HOME ?= $(wildcard ../.toolchain/sdcc-*)
SDCC := $(if $(SDCC_HOME),$(SDCC_HOME)/bin/,)sdcc
MAKEBIN := $(if $(SDCC_HOME),$(SDCC_HOME)/bin/,)makebin

# 0xF800: the bootloader owns 0xF800-0xFFFF and only erases below it.
# 1280 B XRAM: stock firmware addresses XDATA up to 0x04E4, so at least that much exists.
# Version: semver from the nearest vX.Y.Z tag. A release is a tag on main (make bump), so a
# release build reads 0.5.0; a build past the tag reads 0.5.0-2 (2 commits on), + with
# uncommitted changes in fw/. build/version changes only when the version does, forcing a rebuild.
VER := $(or $(shell git describe --tags --match 'v[0-9]*' 2>/dev/null | sed -E 's/^v//; s/-([0-9]+)-g[0-9a-f]+$$/-\1/'),dev)$(shell git diff --quiet HEAD -- . 2>/dev/null || echo +)
$(shell mkdir -p build; echo '$(VER)' | cmp -s - build/version || echo '$(VER)' > build/version)
# Measured on 0.32.0 (full build 61,346 B): --fomit-frame-pointer -295 (reentrant functions without
# locals), --no-xinit-opt -70 (no initialized data to copy, see SDCC traps), --noinvariant -616 (a
# hoisted loop invariant costs more registers and spills than it saves; on 0.36: -279 B, 11 B DATA).
# --max-allocs-per-node, --peep-asm, --peep-return, --nojtbound, --nolospre: no change;
# --noinduction, --nolabelopt: bigger.
# --disable-warning 110: SDCC's "conditional flow changed by optimizer" note (with its EVELYN joke), noise
CFLAGS = -DVERSION='"$(VER)"' --Werror --disable-warning 110 -mmcs51 --model-large --opt-code-size --std-c11 \
         --noinvariant --fomit-frame-pointer --no-xinit-opt \
         --code-size 0xF800 --xram-size 1280 --iram-size 256 $(DEFS) $(CDEFS)
SRC = main.c sys.c lcd.c eeprom.c bk4819.c keys.c radio.c clone.c fm.c scan.c remote.c glyphs.c tones.c help.c
# Hand-written assembly (DEVELOPING.md, Assembly): fw/asm/<fn>.asm, each with its C original behind
# #ifdef C_<FN>. ASM=1 (the default since v0.59.2) links the asm, but a
# function whose C changed since its asm was written is "asm stale" and comes from C (tools/asmcheck.py).
# ASM=0 builds all C.
# CFUN="a b" builds those from C even at ASM=1. make asm: build/td-h3-asm.bin; make cref:
# build/td-h3-c.bin, the all-C image the asm is compared against (tools/asmdiff.py).
ASM ?= 1
LINK := $(shell python3 ../tools/asmcheck.py link $(ASM) "$(CFUN)")
CDEFS := $(shell python3 ../tools/asmcheck.py defs $(ASM) "$(CFUN)")
AS := $(if $(SDCC_HOME),$(SDCC_HOME)/bin/,)sdas8051

all: build/td-h3.bin
rxonly:
	$(MAKE) DEFS=-DNO_TX OUT=td-h3-rx build/td-h3-rx.bin
cref:
	$(MAKE) ASM=0 OUT=td-h3-c build/td-h3-c.bin
asm:
	$(MAKE) ASM=1 OUT=td-h3-asm build/td-h3-asm.bin

OUT ?= td-h3
O = build/$(OUT)
$(shell echo '$(LINK)' | cmp -s - build/$(OUT).asm || echo '$(LINK)' > build/$(OUT).asm)

# The asm's constants and struct offsets, from the C (tools/asmcheck.py inc): rebuilt with the C.
build/asm.inc: $(SRC) fw.h hw.h ../tools/asmcheck.py
	python3 ../tools/asmcheck.py inc && touch $@

build/$(OUT).bin: $(SRC) $(LINK:%=asm/%.asm) $(if $(LINK),build/asm.inc) fw.h hw.h build/version build/$(OUT).asm Makefile   # Makefile: new flags rebuild everything
	@mkdir -p $(O)
	@for f in $(SRC); do $(SDCC) $(CFLAGS) -c $$f -o $(O)/ || exit 1; done
	@for f in $(LINK); do $(AS) -plosgffw $(O)/$$f.rel asm/$$f.asm || exit 1; done
	$(SDCC) $(CFLAGS) $(addprefix $(O)/,$(SRC:.c=.rel) $(LINK:=.rel)) -o $(O)/fw.ihx
	@grep -Eq '00000000 +l_XINIT' $(O)/fw.map || { echo "initialized XDATA found (see XINIT in $(O)/fw.map): use plain assignments"; exit 1; }
	$(MAKEBIN) -s 63488 -p $(O)/fw.ihx $@
	@size=$$(stat -c %s $@); \
	 if [ $$size -gt 63488 ]; then echo "$@: $$size bytes, over 63488: would hit the bootloader"; rm -f $@; exit 1; fi; \
	 echo "$@: $$size bytes ($$((size * 100 / 63488))% of 62 KB)"

# The emulator is a tight Python loop: PyPy runs the suite ~10x faster (13 s vs 2.5 min).
# uv python install pypy3.11 puts it in ~/.local/share/uv; plain python3 works too.
PY := $(shell uv python find pypy3.11 2>/dev/null || echo python3)
test: all rxonly
	$(PY) test_fw.py $(T)
	$(if $(T)$(NOBUDGET),,@python3 ../tools/budget.py)

# What each scarce resource has left and what a byte of it is worth (DEVELOPING.md, Hardware budget).
# The stack figure comes from the last full make test.
budget: all rxonly
	python3 ../tools/budget.py

# Manual screenshots from the real firmware (docs/manual/img), then the Forgejo wiki copy of docs/manual.
# Their own receive-only build with the release number alone (0.39.0, never 0.39.0-14 from a branch
# or a dirty tree), so the manual's pictures show a version someone could have on their radio.
SHOTVER := $(shell git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null | sed 's/^v//')
screenshots: all rxonly
	$(MAKE) VER=$(SHOTVER) DEFS=-DNO_TX OUT=td-h3-shots build/td-h3-shots.bin
	FW_RX=build/td-h3-shots.bin $(PY) ../tools/screens.py capture
	python3 ../tools/screens.py annotate
	cd ../tools/tdh3 && go build -o ../../fw/build/tdh3-shots . && TDH3_SHOT=$(CURDIR)/build/screens/flash.ans TDH3_SHOT_VER=$(SHOTVER) go test -count=1 -run ManualPicture .
	python3 ../tools/screens.py terms

# The demo GIF (docs/img/demo.gif, and .mp4): the boot from the screenshots, then a tour (tools/demo.py).
demo: screenshots
	$(PY) ../tools/demo.py capture
	python3 ../tools/demo.py render

WIKI ?= https://github.com/limehawk/td-h3-fw.wiki.git
wiki:
	rm -rf build/wiki && git clone -q $(WIKI) build/wiki
	cd build/wiki && git rm -rqf --ignore-unmatch . && cp -r ../../../docs/manual/. . && \
	git add -A && (git diff --cached --quiet || git commit -qm "Manual from $$(git -C ../../.. rev-parse --short HEAD)") && \
	git push -q origin HEAD   # the wiki's own branch: GitHub shows only that one

# Tag the commit just made with the next version, then: git push --follow-tags
# BUMP=patch (fixes, default), minor (features), major (breaking: settings or channel layout).
BUMP ?= patch
bump:
	@git diff --quiet HEAD || { echo "commit first"; exit 1; }
	@git fetch -q origin main && [ "$$(git rev-parse HEAD)" = "$$(git rev-parse origin/main)" ] || { echo "release from main as it is on the forge: merge the PR, then git switch main && git pull"; exit 1; }
	@! git describe --exact-match --tags --match 'v[0-9]*' HEAD 2>/dev/null || { echo "HEAD is already tagged"; exit 1; }
	@v=$$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null || echo v0.0.0); \
	 set -- $$(echo $${v#v} | tr . ' '); \
	 case $(BUMP) in major) n=$$(($$1 + 1)).0.0;; minor) n=$$1.$$(($$2 + 1)).0;; patch) n=$$1.$$2.$$(($$3 + 1));; \
	 *) echo "BUMP is patch, minor or major"; exit 1;; esac; \
	 git tag -a v$$n -m v$$n && echo "tagged v$$n"
	@$(MAKE) --no-print-directory all rxonly   # rebuild: the images built before the tag say "-1"

clean:
	rm -rf build

.PHONY: all rxonly cref asm test budget screenshots demo wiki bump clean
