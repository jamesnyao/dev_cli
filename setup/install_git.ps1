if (-not (git config --global user.email)) {
  if ($env:WORK -eq "MSFT") {
    $email = "developer@example.com"
  } else {
    $email = "james.n.yao@gmail.com"
  }
  git config --global user.email "$email"
}
if (-not (git config --global user.name)) {
  git config --global user.name "James Yao"
}
