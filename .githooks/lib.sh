#!/usr/bin/env bash
# Shared helpers for the git hooks and the CI check.

# Print the ERE of disallowed trailers and footers.
blocked_regex() {
  printf '%s' '8J+klnxHZW5lcmF0ZWQgd2l0aC4qQ2xhdWRlfEdlbmVyYXRlZCB3aXRoLipDb2RleHxDby1BdXRob3JlZC1CeTpbWzpzcGFjZTpdXSpDbGF1ZGV8Q28tQXV0aG9yZWQtQnk6W1s6c3BhY2U6XV0qQ29kZXh8Q2xhdWRlLVNlc3Npb246fENvZGV4LVNlc3Npb246fGFudGhyb3BpY1wuY29tfG5vcmVwbHlAYW50aHJvcGljfG9wZW5haVwuY29tfGNsYXVkZVwuYWkvY29kZS9zZXNzaW9u' | base64 --decode
}
