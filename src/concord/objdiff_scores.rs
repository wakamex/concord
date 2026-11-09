//! Print the score and size objdiff gave each symbol of the right-hand object.
//!
//!     objdiff-cli diff -1 TARGET -2 BASE SYMBOL -o RESULT --format proto
//!     objdiff_scores RESULT
//!
//! RESULT is objdiff's `objdiff.diff.DiffResult` protobuf message. For each symbol
//! of its right-hand object this prints one line, `name<TAB>size<TAB>percent`,
//! with an empty percent where objdiff paired the symbol with nothing. The
//! percent is the 32-bit float objdiff stored, in its shortest decimal form, which
//! is also how objdiff's JSON output writes it.
//!
//! objdiff's result holds every instruction row of every symbol, megabytes for a
//! large unit, and a search needs two numbers per symbol. This reads the wire
//! format directly, skipping every other field without decoding it, and needs
//! only the standard library: `rustc --edition 2021 -O objdiff_scores.rs`.

use std::io::{self, BufWriter, Write};
use std::{env, fs, process};

enum Value<'a> {
    Number(u64),
    Bytes(&'a [u8]),
}

/// The fields of one protobuf message, in order.
struct Fields<'a> {
    data: &'a [u8],
}

impl<'a> Fields<'a> {
    fn varint(&mut self) -> Option<u64> {
        let mut value = 0u64;
        for shift in (0..64).step_by(7) {
            let (&byte, rest) = self.data.split_first()?;
            self.data = rest;
            value |= u64::from(byte & 0x7f) << shift;
            if byte < 0x80 {
                return Some(value);
            }
        }
        None
    }

    fn take(&mut self, count: usize) -> Option<&'a [u8]> {
        if count > self.data.len() {
            return None;
        }
        let (taken, rest) = self.data.split_at(count);
        self.data = rest;
        Some(taken)
    }
}

impl<'a> Iterator for Fields<'a> {
    /// A field's number and value, or None for a message this cannot read.
    type Item = Option<(u64, Value<'a>)>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.data.is_empty() {
            return None;
        }
        let field = (|| {
            let key = self.varint()?;
            let value = match key & 7 {
                0 => Value::Number(self.varint()?),
                1 => Value::Number(u64::from_le_bytes(self.take(8)?.try_into().ok()?)),
                2 => {
                    let length = usize::try_from(self.varint()?).ok()?;
                    Value::Bytes(self.take(length)?)
                }
                5 => Value::Number(u64::from(u32::from_le_bytes(self.take(4)?.try_into().ok()?))),
                _ => return None,
            };
            Some((key >> 3, value))
        })();
        if field.is_none() {
            self.data = &[];
        }
        Some(field)
    }
}

/// The messages stored in field `number` of `message`.
fn messages<'a>(message: &'a [u8], number: u64) -> impl Iterator<Item = Option<&'a [u8]>> {
    Fields { data: message }.filter_map(move |field| match field {
        Some((found, Value::Bytes(bytes))) if found == number => Some(Some(bytes)),
        Some(_) => None,
        None => Some(None),
    })
}

fn scores(result: &[u8], out: &mut impl Write) -> Option<()> {
    for object in messages(result, 2) {
        // DiffResult.right
        for symbol in messages(object?, 2) {
            // DiffObject.symbols
            let (mut name, mut size, mut percent) = (&[][..], 0u64, None);
            for field in (Fields { data: symbol? }) {
                match field? {
                    (1, Value::Bytes(bytes)) => name = bytes,
                    (4, Value::Number(value)) => size = value,
                    (7, Value::Number(bits)) => percent = Some(f32::from_bits(u32::try_from(bits).ok()?)),
                    _ => {}
                }
            }
            out.write_all(name).ok()?;
            match percent {
                Some(percent) => writeln!(out, "\t{size}\t{percent}").ok()?,
                None => writeln!(out, "\t{size}\t").ok()?,
            }
        }
    }
    out.flush().ok()
}

fn main() {
    let Some(path) = env::args().nth(1) else {
        eprintln!("usage: objdiff_scores RESULT");
        process::exit(2);
    };
    let result = fs::read(&path).unwrap_or_else(|error| {
        eprintln!("{path}: {error}");
        process::exit(1);
    });
    if scores(&result, &mut BufWriter::new(io::stdout().lock())).is_none() {
        eprintln!("{path}: not a complete objdiff DiffResult message");
        process::exit(1);
    }
}
