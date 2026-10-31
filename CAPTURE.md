# Capturing the Rocco app's API traffic with Proxyman

Goal: record the HTTPS requests the Rocco iOS app makes with your own account,
so the Home Assistant integration can make the same calls.

## 1. Set up Proxyman on the Mac

1. Open Proxyman.
2. Menu: **Certificate > Install Certificate on iOS > Physical Devices...**
   Keep this window open. It shows your Mac's IP and proxy port (usually 9090).

## 2. Point the iPhone at Proxyman

Mac and iPhone must be on the same Wi-Fi.

1. iPhone **Settings > Wi-Fi > (i) on your network > Configure Proxy > Manual**.
   Server = the Mac's IP (e.g. 192.168.1.20), Port = 9090.
2. In Safari on the iPhone, open `http://proxy.man/ssl` and install the profile.
3. **Settings > General > VPN & Device Management** > install the Proxyman profile.
4. **Settings > General > About > Certificate Trust Settings** > enable full trust
   for the Proxyman CA.

## 3. Enable SSL decryption for Rocco hosts

In Proxyman, right-click any Rocco request (or use **Tools > SSL Proxying List**)
and add:

```
*.roccofridge.com
roccofridge.com
```

If you see requests to other hosts while using the app (for example an AWS IoT,
MQTT, Firebase or Clerk host on another domain), add those too.

## 4. Do these actions in the app, in this order

Starting fresh makes the auth flow visible, so log out of the app first.

1. Log in.
2. Open the fridge so its status screen loads. Wait 30 seconds so polling shows up.
3. Change the **upper zone** target temperature by one degree.
4. Change the **lower zone** target temperature by one degree.
5. Switch cooling mode: Quiet, then Smart, then Party.
6. Turn the light on, change its brightness, turn it off.
7. Open the fridge door for a few seconds, close it. Watch whether anything
   new arrives.
8. Open the Sight System / inside view.
9. Change temperatures back to what you had.

If something fails or the app shows "no connection" once the proxy is on, see
the troubleshooting section.

## 5. Export and send it

1. Filter Proxyman to `roccofridge` (plus any extra hosts from step 3).
2. Select all those requests > right-click > **Export > HAR**.
3. Save it somewhere outside this repo, such as `~/Desktop/capture.har`.

Also check the **WebSocket** tab for any socket connections and include those
messages. Live status often arrives that way instead of by polling.

### Redaction

- Your **password** appears in the login request body. Replace it with `REDACTED`.
- Leave tokens in place if you're comfortable, since they're in a local file.
  Otherwise mask the middle of each token but keep the first and last few
  characters, so it's clear which token is reused where.
- Leave fridge IDs, header names and response bodies intact. Those are what
  the integration needs.

## 6. Clean up

Turn the iPhone's Wi-Fi proxy back to **Off** and, optionally, disable trust for
the Proxyman certificate.

## Troubleshooting

- **App can't connect once the proxy is on:** the app probably uses certificate
  pinning. Tell me which hosts fail. The login host alone may still be visible,
  and there are other options worth discussing.
- **No requests appear at all:** check the iPhone proxy IP and port, and that the
  Mac's firewall allows Proxyman.
- **Requests appear but bodies are encrypted:** the host isn't in the SSL
  Proxying List yet.

## Round 2: filling the remaining gaps

Captures so far cover sign-in, inventory, the light, the lower zone and cooling
modes. Still missing:

1. **Live state.** The app uses AWS IoT for this. Add `*.amazonaws.com` to the
   SSL Proxying List. Then fully quit the app, wait an hour or log out and back
   in so it fetches fresh AWS credentials, and open the fridge's control screen.
   Look for `cognito-identity` requests and any host containing `iot`.
2. **Brightness:** change the light brightness.
3. **Upper zone:** change the upper zone temperature.
4. **Sight System:** open the inside view.
5. **Other modes:** check whether a Quiet mode exists and switch to it.
