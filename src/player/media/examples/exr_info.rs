//! `exr_info <file.exr> [<x> <y>]`: an EXR's layers as `ExrSequence` sees
//! them, how long a decode takes, each shown channel's min/max/mean, and the
//! values of the pixel at (x, y). For checking our reading of a render
//! against another reader, e.g. Houdini's
//! `hoiiotool file.exr --stats --cut 1x1+X+Y --dumpdata`.

use std::time::Instant;

use th_media::{ExrSequence, ExrView, Pixels};

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let Some(path) = args.first() else {
        eprintln!("usage: exr_info <file.exr> [<x> <y>]");
        std::process::exit(2);
    };
    let at: Option<(usize, usize)> = match (args.get(1), args.get(2)) {
        (Some(x), Some(y)) => Some((x.parse().expect("x"), y.parse().expect("y"))),
        _ => None,
    };
    let sequence = ExrSequence::new(vec![path.into()]);
    let layers = sequence.layers().expect("reading the header");
    println!("default view: {:?}", ExrSequence::default_view(&layers));
    for layer in &layers {
        println!("layer '{}': {:?}", layer.name, layer.channels);
        let started = Instant::now();
        let view = ExrView { layer: layer.name.clone(), channel: None };
        let frame = sequence.decode(0, &view).expect("decoding");
        let Pixels::Half { data, channels } = &*frame.pixels else { unreachable!() };
        let shown: Vec<&str> = layer.shown(None).unwrap().iter().map(|&i| layer.channels[i].as_str()).collect();
        println!("  {}x{}, shown {:?}, decoded in {:.0} ms", frame.width, frame.height, shown, started.elapsed().as_secs_f64() * 1e3);
        for (slot, name) in shown.iter().enumerate() {
            let values = data.iter().skip(slot).step_by(*channels).map(|v| v.to_f64());
            let (mut min, mut max, mut sum, mut n) = (f64::INFINITY, f64::NEG_INFINITY, 0.0, 0usize);
            for v in values {
                min = min.min(v);
                max = max.max(v);
                sum += v;
                n += 1;
            }
            println!("  {name}: min {min:.6} max {max:.6} mean {:.6}", sum / n as f64);
        }
        if let Some((x, y)) = at {
            let i = (y * frame.width as usize + x) * channels;
            let values: Vec<f32> = data[i..i + channels].iter().map(|v| v.to_f32()).collect();
            println!("  pixel ({x}, {y}): {values:?}");
        }
    }
}
