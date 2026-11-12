//! Undo and redo: a stack of whole documents (an edit is small).

use crate::document::Edit;
use crate::ops::{self, Op, Refused, Sources};

/// How many steps back undo reaches.
pub const DEPTH: usize = 1000;

#[derive(Debug, Clone)]
pub struct History<S> {
    past: Vec<Edit<S>>,
    future: Vec<Edit<S>>,
}

impl<S> Default for History<S> {
    fn default() -> Self {
        History { past: Vec::new(), future: Vec::new() }
    }
}

impl<S: Clone + PartialEq> History<S> {
    pub fn new() -> Self {
        Self::default()
    }

    /// Apply `op` to `edit`, remembering what it was. A refused op changes
    /// nothing, history included.
    pub fn apply(&mut self, edit: &mut Edit<S>, op: &Op<S>, sources: &impl Sources<S>) -> Result<(), Refused> {
        let next = ops::apply(edit, op, sources)?;
        self.past.push(std::mem::replace(edit, next));
        if self.past.len() > DEPTH {
            self.past.remove(0);
        }
        self.future.clear();
        Ok(())
    }

    /// Apply `ops` in order as one step: undo takes them all back at once.
    /// One refused op refuses them all and changes nothing; no ops is no
    /// step.
    pub fn apply_all(&mut self, edit: &mut Edit<S>, ops: &[Op<S>], sources: &impl Sources<S>) -> Result<(), Refused> {
        if ops.is_empty() {
            return Ok(());
        }
        let mut next = ops::apply(edit, &ops[0], sources)?;
        for op in &ops[1..] {
            next = ops::apply(&next, op, sources)?;
        }
        self.past.push(std::mem::replace(edit, next));
        if self.past.len() > DEPTH {
            self.past.remove(0);
        }
        self.future.clear();
        Ok(())
    }

    /// Back one step; false when there is none.
    pub fn undo(&mut self, edit: &mut Edit<S>) -> bool {
        match self.past.pop() {
            Some(before) => {
                self.future.push(std::mem::replace(edit, before));
                true
            }
            None => false,
        }
    }

    /// Forward one undone step; false when there is none.
    pub fn redo(&mut self, edit: &mut Edit<S>) -> bool {
        match self.future.pop() {
            Some(after) => {
                self.past.push(std::mem::replace(edit, after));
                true
            }
            None => false,
        }
    }

    pub fn can_undo(&self) -> bool {
        !self.past.is_empty()
    }

    pub fn can_redo(&self) -> bool {
        !self.future.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::document::Source;
    use crate::ops::tests::{Lengths, any_op, choose, edits};
    use proptest::prelude::*;

    proptest! {
        /// Undo after any run of ops gives back every earlier edit exactly,
        /// newest first; redo then gives them again, oldest first.
        #[test]
        fn undo_restores_and_redo_reapplies(edit in edits(), tries in prop::collection::vec(any_op(), 1..30)) {
            let mut edit: Edit<Source> = edit;
            let mut history = History::new();
            let mut seen = vec![edit.clone()];
            for op in tries {
                let Some(op) = choose(&edit, &op) else { continue };
                if history.apply(&mut edit, &op, &Lengths).is_ok() {
                    seen.push(edit.clone());
                }
            }
            for want in seen.iter().rev().skip(1) {
                prop_assert!(history.undo(&mut edit));
                prop_assert_eq!(&edit, want);
            }
            prop_assert!(!history.undo(&mut edit));
            for want in seen.iter().skip(1) {
                prop_assert!(history.redo(&mut edit));
                prop_assert_eq!(&edit, want);
            }
            prop_assert!(!history.redo(&mut edit));
        }

        /// A run of ops applied together is one step: it ends where applying
        /// them one by one ends, and a single undo goes back to the start.
        /// A refused one leaves the edit and the history as they were.
        #[test]
        fn ops_applied_together_undo_together(edit in edits(), tries in prop::collection::vec(any_op(), 1..10)) {
            let start: Edit<Source> = edit;
            let mut each = start.clone();
            let mut ops = Vec::new();
            for op in tries {
                let Some(op) = choose(&each, &op) else { continue };
                if let Ok(next) = ops::apply(&each, &op, &Lengths) {
                    each = next;
                    ops.push(op);
                }
            }
            let mut edit = start.clone();
            let mut history = History::new();
            history.apply_all(&mut edit, &ops, &Lengths).unwrap();
            prop_assert_eq!(&edit, &each);
            if !ops.is_empty() {
                prop_assert!(history.undo(&mut edit));
                prop_assert_eq!(&edit, &start);
                prop_assert!(history.redo(&mut edit));
                prop_assert_eq!(&edit, &each);
            }
            prop_assert!(!history.redo(&mut edit));
        }
    }

    #[test]
    fn a_refused_op_refuses_the_whole_step() {
        let empty: Edit<Source> = Edit::new(24, [1920, 1080], "01:00:00:00");
        let mut edit = empty.clone();
        let mut history = History::new();
        let bad = Op::Split { clip: crate::document::ClipRef { track: crate::document::TrackRef::video(0), index: 0 }, at: 1 };
        assert!(history.apply_all(&mut edit, &[bad], &Lengths).is_err());
        assert_eq!(edit, empty);
        assert!(!history.can_undo());
    }
}
