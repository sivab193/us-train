//! GTFS times are stored as INTEGER seconds-from-midnight and may exceed 86400
//! (e.g. 91800 == 25:30:00 == 01:30 the following service day). These helpers
//! convert between the integer form and a human-readable "HH:MM" string.
//!
//! We render the GTFS clock value (which can read "25:30"), not a wall-clock
//! time, because the "+1 day" information is meaningful for a timetable.

/// 91800 -> Some("25:30"). None -> None.
pub fn to_readable(seconds: Option<i32>) -> Option<String> {
    seconds.map(|s| {
        let h = s / 3600;
        let m = (s % 3600) / 60;
        format!("{:02}:{:02}", h, m)
    })
}

/// Parse "HH:MM" or "HH:MM:SS" into seconds-from-midnight. Blank -> None.
pub fn parse(hhmm: &str) -> Result<Option<i32>, String> {
    let t = hhmm.trim();
    if t.is_empty() {
        return Ok(None);
    }
    let parts: Vec<&str> = t.split(':').collect();
    if parts.len() < 2 {
        return Err(format!("invalid time: {hhmm}"));
    }
    let h: i32 = parts[0].parse().map_err(|_| format!("invalid time: {hhmm}"))?;
    let m: i32 = parts[1].parse().map_err(|_| format!("invalid time: {hhmm}"))?;
    let s: i32 = if parts.len() >= 3 {
        parts[2].parse().map_err(|_| format!("invalid time: {hhmm}"))?
    } else {
        0
    };
    Ok(Some(h * 3600 + m * 60 + s))
}

/// Whether a GTFS time rolls past midnight (>= 24:00:00).
pub fn is_next_day(seconds: Option<i32>) -> bool {
    matches!(seconds, Some(s) if s >= 86400)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn renders_past_midnight() {
        assert_eq!(to_readable(Some(91800)).unwrap(), "25:30");
        assert_eq!(to_readable(Some(28800)).unwrap(), "08:00");
        assert_eq!(to_readable(None), None);
    }

    #[test]
    fn parses_both_forms() {
        assert_eq!(parse("08:00").unwrap(), Some(28800));
        assert_eq!(parse("25:30:00").unwrap(), Some(91800));
        assert_eq!(parse("").unwrap(), None);
        assert!(parse("nope").is_err());
    }

    #[test]
    fn next_day_flag() {
        assert!(is_next_day(Some(86400)));
        assert!(is_next_day(Some(91800)));
        assert!(!is_next_day(Some(86399)));
        assert!(!is_next_day(None));
    }
}
