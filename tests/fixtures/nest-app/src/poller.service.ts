import { Injectable } from '@nestjs/common';

import { GreeterService } from './greeter.service';

@Injectable()
export class PollerService {
  constructor(private readonly greeter: GreeterService) {}

  /** Calls `url` until it fails: code that waits on I/O and never ends by itself. */
  async poll(url: string): Promise<number> {
    let calls = 0;
    for (;;) {
      await fetch(url);
      calls += 1;
    }
  }

  async greetLater(name: string): Promise<string> {
    return this.greeter.greet(name);
  }
}
