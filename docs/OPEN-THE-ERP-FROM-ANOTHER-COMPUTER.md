# Open the eSway ERP from another computer or phone (any network)

The ERP runs on the office PC (name **DESKTOP-TOUF7I0**). It is private: it can only be opened from devices that joined the company's Tailscale network. Do this once per device.

1. Install **Tailscale** from https://tailscale.com/download (Windows, Mac, Android, iPhone, Linux).
2. Open it and **sign in with the account the office PC uses** (`maniars4524@...`) - or, if you have your own Tailscale account, ask the administrator to **share** the PC with you (see below).
3. Wait until Tailscale shows **Connected** and lists `desktop-touf7i0`.
4. In the browser open **http://desktop-touf7i0:8000/erp/login** (or http://100.120.249.72:8000/erp/login). Use `http://`, keep `:8000`.
5. Sign in with your license number, user name and password.

If it does not open: in Command Prompt run `ping 100.120.249.72`. No reply = Tailscale is not connected on your device (or is signed in to another account). The office PC must be on and the ERP started (RUN-LOCAL.cmd).

## For the administrator: let someone with their own Tailscale account in

Open https://login.tailscale.com/admin/machines, click the three dots next to `desktop-touf7i0`, choose **Share...**, copy the invite link and send it to that person. They accept it with their own account and then use the address above.
